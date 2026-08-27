#include "CargoBayController.h"

#include <array>
#include <limits>

#include <QtCore/QApplicationStatic>
#include <QtCore/QDateTime>
#include <QtCore/QDir>
#include <QtCore/QFile>
#include <QtCore/QFileInfo>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QSettings>
#include <QtCore/QStandardPaths>
#include <QtPositioning/QGeoCoordinate>

#include "FirmwarePlugin/PX4/PX4FirmwarePlugin.h"
#include "Vehicle/MultiVehicleManager.h"
#include "Vehicle/Vehicle.h"

namespace {
constexpr char SETTINGS_GROUP[] = "FlyCam/CargoBay";
constexpr char ACTUATOR_NUMBER_KEY[] = "actuatorNumber";
constexpr char OPEN_VALUE_KEY[] = "openValue";
constexpr char CLOSED_VALUE_KEY[] = "closedValue";
constexpr int MINIMUM_ACTUATOR_NUMBER = 1;
constexpr int MAXIMUM_ACTUATOR_NUMBER = 6;
}

Q_APPLICATION_STATIC(CargoBayController, _cargoBayControllerInstance);

CargoBayController::CargoBayController(QObject* parent)
    : QObject(parent)
{
    _loadConfiguration();

    const QString appDataPath = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    _logFilePath = QDir(appDataPath).filePath(QStringLiteral("logs/cargo-bay.jsonl"));

    MultiVehicleManager* manager = MultiVehicleManager::instance();
    (void) connect(manager, &MultiVehicleManager::activeVehicleChanged, this, &CargoBayController::_setVehicle);
    _setVehicle(manager->activeVehicle());
}

CargoBayController* CargoBayController::instance()
{
    return _cargoBayControllerInstance();
}

bool CargoBayController::px4Vehicle() const
{
    return _vehicle && qobject_cast<PX4FirmwarePlugin*>(_vehicle->firmwarePlugin());
}

bool CargoBayController::openAllowed() const
{
    return px4Vehicle() && _vehicle->isInitialConnectComplete()
        && !_vehicle->armed() && !_vehicle->flying() && !_commandInProgress;
}

bool CargoBayController::closeAllowed() const
{
    return px4Vehicle() && _vehicle->isInitialConnectComplete() && !_commandInProgress;
}

QString CargoBayController::statusText() const
{
    if (!_vehicle) {
        return tr("БПЛА не подключён");
    }
    if (!px4Vehicle()) {
        return tr("Требуется прошивка PX4");
    }
    if (!_vehicle->isInitialConnectComplete()) {
        return tr("Ожидание полной загрузки параметров");
    }
    if (_commandInProgress) {
        return _pendingOpen ? tr("Отправляется команда открытия") : tr("Отправляется команда закрытия");
    }
    if (!_statusMessage.isEmpty()) {
        return _statusMessage;
    }
    if (_vehicle->flying()) {
        return tr("Открытие заблокировано в полёте");
    }
    if (_vehicle->armed()) {
        return tr("Для открытия разоружите БПЛА");
    }
    if (_commandedStateKnown) {
        return _commandedOpen ? tr("PX4 подтвердил команду открытия") : tr("PX4 подтвердил команду закрытия");
    }
    return tr("Готов к управлению отсеком");
}

void CargoBayController::setActuatorNumber(int actuatorNumber)
{
    const int boundedValue = qBound(MINIMUM_ACTUATOR_NUMBER, actuatorNumber, MAXIMUM_ACTUATOR_NUMBER);
    if (_actuatorNumber == boundedValue) {
        return;
    }
    _actuatorNumber = boundedValue;
    _saveConfiguration();
    emit configurationChanged();
}

void CargoBayController::setOpenValue(double openValue)
{
    const double boundedValue = qBound(-1.0, openValue, 1.0);
    if (qFuzzyCompare(_openValue, boundedValue)) {
        return;
    }
    _openValue = boundedValue;
    _saveConfiguration();
    emit configurationChanged();
}

void CargoBayController::setClosedValue(double closedValue)
{
    const double boundedValue = qBound(-1.0, closedValue, 1.0);
    if (qFuzzyCompare(_closedValue, boundedValue)) {
        return;
    }
    _closedValue = boundedValue;
    _saveConfiguration();
    emit configurationChanged();
}

void CargoBayController::requestOpen()
{
    if (!openAllowed()) {
        _appendAuditEvent(QStringLiteral("open-blocked"), statusText());
        emit statusChanged();
        return;
    }
    _sendActuatorCommand(true);
}

void CargoBayController::requestClose()
{
    if (!closeAllowed()) {
        _appendAuditEvent(QStringLiteral("close-blocked"), statusText());
        emit statusChanged();
        return;
    }
    _sendActuatorCommand(false);
}

void CargoBayController::resetConfiguration()
{
    _actuatorNumber = 1;
    _openValue = 1.0;
    _closedValue = -1.0;
    _saveConfiguration();
    emit configurationChanged();
}

void CargoBayController::_commandAckEntry(void* resultHandlerData,
                                          int,
                                          const mavlink_command_ack_t& ack,
                                          VehicleTypes::MavCmdResultFailureCode_t failureCode)
{
    auto* controller = static_cast<CargoBayController*>(resultHandlerData);
    if (controller) {
        controller->_handleCommandAck(ack, failureCode);
    }
}

void CargoBayController::_setVehicle(Vehicle* vehicle)
{
    if (_vehicle == vehicle) {
        return;
    }

    if (_vehicle) {
        disconnect(_vehicle, nullptr, this, nullptr);
    }

    _vehicle = vehicle;
    _statusMessage.clear();
    _commandedStateKnown = false;
    _setCommandInProgress(false);

    if (_vehicle) {
        (void) connect(_vehicle, &Vehicle::armedChanged, this, &CargoBayController::_refreshAvailability);
        (void) connect(_vehicle, &Vehicle::flyingChanged, this, &CargoBayController::_refreshAvailability);
        (void) connect(_vehicle, &Vehicle::initialConnectComplete, this, &CargoBayController::_refreshAvailability);
        (void) connect(_vehicle, &Vehicle::firmwareTypeChanged, this, &CargoBayController::_refreshAvailability);
        (void) connect(_vehicle, &QObject::destroyed, this, [this]() { _setVehicle(nullptr); });
    }

    emit availabilityChanged();
    emit commandedStateChanged();
    emit statusChanged();
}

void CargoBayController::_refreshAvailability()
{
    _statusMessage.clear();
    emit availabilityChanged();
    emit statusChanged();
}

void CargoBayController::_sendActuatorCommand(bool open)
{
    if (!_vehicle) {
        return;
    }

    const float value = static_cast<float>(open ? _openValue : _closedValue);
    const float unchanged = std::numeric_limits<float>::quiet_NaN();
    std::array<float, MAXIMUM_ACTUATOR_NUMBER> actuatorValues = {
        unchanged, unchanged, unchanged, unchanged, unchanged, unchanged
    };
    actuatorValues[static_cast<size_t>(_actuatorNumber - 1)] = value;

    _pendingOpen = open;
    _setStatusMessage(QString());
    _setCommandInProgress(true);
    _appendAuditEvent(open ? QStringLiteral("open-requested") : QStringLiteral("close-requested"),
                      QStringLiteral("sent"),
                      value);

    Vehicle::MavCmdAckHandlerInfo_t handlerInfo = {};
    handlerInfo.resultHandler = _commandAckEntry;
    handlerInfo.resultHandlerData = this;

    _vehicle->sendMavCommandWithHandler(
        &handlerInfo,
        MAV_COMP_ID_AUTOPILOT1,
        MAV_CMD_DO_SET_ACTUATOR,
        actuatorValues[0],
        actuatorValues[1],
        actuatorValues[2],
        actuatorValues[3],
        actuatorValues[4],
        actuatorValues[5],
        0.0f);
}

void CargoBayController::_handleCommandAck(const mavlink_command_ack_t& ack,
                                           VehicleTypes::MavCmdResultFailureCode_t failureCode)
{
    const bool accepted = (failureCode == VehicleTypes::MavCmdResultCommandResultOnly)
        && (ack.result == MAV_RESULT_ACCEPTED);

    _setCommandInProgress(false);

    if (accepted) {
        _commandedOpen = _pendingOpen;
        _commandedStateKnown = true;
        _setStatusMessage(_pendingOpen ? tr("Команда открытия подтверждена PX4")
                                       : tr("Команда закрытия подтверждена PX4"));
        emit commandedStateChanged();
        _appendAuditEvent(_pendingOpen ? QStringLiteral("open-acknowledged")
                                      : QStringLiteral("close-acknowledged"),
                          QStringLiteral("accepted"),
                          _pendingOpen ? _openValue : _closedValue);
        return;
    }

    QString errorText;
    if (failureCode == VehicleTypes::MavCmdResultFailureNoResponseToCommand) {
        errorText = tr("Нет подтверждения от PX4");
    } else if (failureCode == VehicleTypes::MavCmdResultFailureDuplicateCommand) {
        errorText = tr("Предыдущая команда ещё выполняется");
    } else {
        errorText = tr("PX4 отклонил команду, код %1").arg(ack.result);
    }
    _setStatusMessage(errorText);
    _appendAuditEvent(_pendingOpen ? QStringLiteral("open-failed") : QStringLiteral("close-failed"),
                      errorText,
                      _pendingOpen ? _openValue : _closedValue);
}

void CargoBayController::_setCommandInProgress(bool commandInProgress)
{
    if (_commandInProgress == commandInProgress) {
        return;
    }
    _commandInProgress = commandInProgress;
    emit commandInProgressChanged();
    emit availabilityChanged();
    emit statusChanged();
}

void CargoBayController::_setStatusMessage(const QString& statusMessage)
{
    if (_statusMessage == statusMessage) {
        return;
    }
    _statusMessage = statusMessage;
    emit statusChanged();
}

void CargoBayController::_loadConfiguration()
{
    QSettings settings;
    settings.beginGroup(QString::fromLatin1(SETTINGS_GROUP));
    _actuatorNumber = qBound(MINIMUM_ACTUATOR_NUMBER,
                             settings.value(QString::fromLatin1(ACTUATOR_NUMBER_KEY), 1).toInt(),
                             MAXIMUM_ACTUATOR_NUMBER);
    _openValue = qBound(-1.0, settings.value(QString::fromLatin1(OPEN_VALUE_KEY), 1.0).toDouble(), 1.0);
    _closedValue = qBound(-1.0, settings.value(QString::fromLatin1(CLOSED_VALUE_KEY), -1.0).toDouble(), 1.0);
    settings.endGroup();
}

void CargoBayController::_saveConfiguration() const
{
    QSettings settings;
    settings.beginGroup(QString::fromLatin1(SETTINGS_GROUP));
    settings.setValue(QString::fromLatin1(ACTUATOR_NUMBER_KEY), _actuatorNumber);
    settings.setValue(QString::fromLatin1(OPEN_VALUE_KEY), _openValue);
    settings.setValue(QString::fromLatin1(CLOSED_VALUE_KEY), _closedValue);
    settings.endGroup();
}

void CargoBayController::_appendAuditEvent(const QString& eventName,
                                           const QString& result,
                                           std::optional<double> requestedValue)
{
    const QFileInfo logInfo(_logFilePath);
    QDir().mkpath(logInfo.absolutePath());

    QJsonObject event {
        {QStringLiteral("timestampUtc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)},
        {QStringLiteral("event"), eventName},
        {QStringLiteral("eventType"), QStringLiteral("cargo-bay")},
        {QStringLiteral("result"), result},
        {QStringLiteral("actuatorNumber"), _actuatorNumber},
    };

    if (requestedValue.has_value()) {
        event.insert(QStringLiteral("requestedValue"), requestedValue.value());
    }

    if (_vehicle) {
        event.insert(QStringLiteral("vehicleId"), _vehicle->id());
        event.insert(QStringLiteral("armed"), _vehicle->armed());
        event.insert(QStringLiteral("flying"), _vehicle->flying());
        event.insert(QStringLiteral("flightMode"), _vehicle->flightMode());
        const QGeoCoordinate coordinate = _vehicle->coordinate();
        if (coordinate.isValid()) {
            event.insert(QStringLiteral("latitude"), coordinate.latitude());
            event.insert(QStringLiteral("longitude"), coordinate.longitude());
            event.insert(QStringLiteral("altitude"), coordinate.altitude());
        }
    }

    QFile logFile(_logFilePath);
    if (logFile.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
        logFile.write(QJsonDocument(event).toJson(QJsonDocument::Compact));
        logFile.write("\n");
    }
    emit auditEventRecorded(event);
}

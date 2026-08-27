#include "DispatcherClient.h"

#include <QtCore/QApplicationStatic>
#include <QtCore/QDateTime>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QSettings>
#include <QtCore/QUrl>
#include <QtNetwork/QNetworkReply>
#include <QtNetwork/QNetworkRequest>
#include <QtPositioning/QGeoCoordinate>

#include "CargoBayController.h"
#include "Vehicle/MultiVehicleManager.h"
#include "Vehicle/Vehicle.h"

namespace {
constexpr char SETTINGS_GROUP[] = "FlyCam/Dispatcher";
constexpr char ENABLED_KEY[] = "enabled";
constexpr char SERVER_URL_KEY[] = "serverUrl";
constexpr char API_KEY[] = "apiKey";
constexpr int TELEMETRY_INTERVAL_MS = 1000;
constexpr int REQUEST_TIMEOUT_MS = 4000;
}

Q_APPLICATION_STATIC(DispatcherClient, _dispatcherClientInstance);

DispatcherClient::DispatcherClient(QObject* parent)
    : QObject(parent)
{
    _loadConfiguration();

    _timer.setInterval(TELEMETRY_INTERVAL_MS);
    _timer.setSingleShot(false);
    (void) connect(&_timer, &QTimer::timeout, this, &DispatcherClient::_sendTelemetry);
    (void) connect(CargoBayController::instance(),
                   &CargoBayController::auditEventRecorded,
                   this,
                   &DispatcherClient::_sendEvent);

    MultiVehicleManager* manager = MultiVehicleManager::instance();
    (void) connect(manager, &MultiVehicleManager::activeVehicleChanged, this, &DispatcherClient::_setVehicle);
    _setVehicle(manager->activeVehicle());

    if (_enabled) {
        _timer.start();
    }
}

DispatcherClient* DispatcherClient::instance()
{
    return _dispatcherClientInstance();
}

QString DispatcherClient::statusText() const
{
    if (!_enabled) {
        return tr("Диспетчеризация выключена");
    }
    if (!_vehicle) {
        return tr("Диспетчер: ожидание БПЛА");
    }
    if (!_lastError.isEmpty()) {
        return tr("Диспетчер: %1").arg(_lastError);
    }
    if (_serverReachable) {
        return tr("Диспетчер: телеметрия передаётся");
    }
    return tr("Диспетчер: проверка соединения");
}

void DispatcherClient::setEnabled(bool enabled)
{
    if (_enabled == enabled) {
        return;
    }
    _enabled = enabled;
    _saveConfiguration();

    if (_enabled) {
        _timer.start();
        _sendTelemetry();
    } else {
        _timer.stop();
        if (_reply) {
            _reply->abort();
        }
        _setConnectionState(false);
    }

    emit configurationChanged();
    emit connectionStateChanged();
}

void DispatcherClient::setServerUrl(const QString& serverUrl)
{
    QString normalizedUrl = serverUrl.trimmed();
    while (normalizedUrl.endsWith('/')) {
        normalizedUrl.chop(1);
    }
    if (_serverUrl == normalizedUrl) {
        return;
    }
    _serverUrl = normalizedUrl;
    _saveConfiguration();
    _setConnectionState(false);
    emit configurationChanged();
}

void DispatcherClient::setApiKey(const QString& apiKey)
{
    if (_apiKey == apiKey) {
        return;
    }
    _apiKey = apiKey;
    _saveConfiguration();
    _setConnectionState(false);
    emit configurationChanged();
}

void DispatcherClient::sendNow()
{
    _sendTelemetry();
}

void DispatcherClient::_setVehicle(Vehicle* vehicle)
{
    if (_vehicle == vehicle) {
        return;
    }

    if (_vehicle) {
        disconnect(_vehicle, nullptr, this, nullptr);
    }
    _vehicle = vehicle;

    if (_vehicle) {
        (void) connect(_vehicle, &QObject::destroyed, this, [this]() { _setVehicle(nullptr); });
    }

    emit connectionStateChanged();
}

void DispatcherClient::_sendTelemetry()
{
    if (!_enabled || !_vehicle || _reply) {
        return;
    }

    const QUrl endpoint(_serverUrl + QStringLiteral("/api/v1/telemetry"));
    if (!endpoint.isValid() || (endpoint.scheme() != QStringLiteral("http")
                                && endpoint.scheme() != QStringLiteral("https"))) {
        _setConnectionState(false, tr("некорректный адрес сервера"));
        return;
    }

    const QGeoCoordinate coordinate = _vehicle->coordinate();
    QJsonObject telemetry {
        {QStringLiteral("timestampUtc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)},
        {QStringLiteral("vehicleId"), _vehicle->id()},
        {QStringLiteral("flightMode"), _vehicle->flightMode()},
        {QStringLiteral("armed"), _vehicle->armed()},
        {QStringLiteral("flying"), _vehicle->flying()},
        {QStringLiteral("mavlinkLossPercent"), _vehicle->mavlinkLossPercent()},
        {QStringLiteral("cargoCommandedOpen"), CargoBayController::instance()->commandedOpen()},
        {QStringLiteral("cargoCommandedStateKnown"), CargoBayController::instance()->commandedStateKnown()},
        {QStringLiteral("cargoFeedbackAvailable"), CargoBayController::instance()->feedbackAvailable()},
        {QStringLiteral("cargoStatus"), CargoBayController::instance()->statusText()},
    };
    if (coordinate.isValid()) {
        telemetry.insert(QStringLiteral("latitude"), coordinate.latitude());
        telemetry.insert(QStringLiteral("longitude"), coordinate.longitude());
        telemetry.insert(QStringLiteral("altitude"), coordinate.altitude());
    }

    QNetworkRequest request(endpoint);
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    request.setHeader(QNetworkRequest::UserAgentHeader, QStringLiteral("FlyCam-Drone-Control-Center/1.0"));
    request.setTransferTimeout(REQUEST_TIMEOUT_MS);
    if (!_apiKey.isEmpty()) {
        request.setRawHeader("X-API-Key", _apiKey.toUtf8());
    }

    _reply = _networkManager.post(request, QJsonDocument(telemetry).toJson(QJsonDocument::Compact));
    (void) connect(_reply, &QNetworkReply::finished, this, &DispatcherClient::_handleReply);
}

void DispatcherClient::_handleReply()
{
    if (!_reply) {
        return;
    }

    const int statusCode = _reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
    const bool success = (_reply->error() == QNetworkReply::NoError) && (statusCode >= 200) && (statusCode < 300);
    const QString errorText = success ? QString() : (_reply->error() == QNetworkReply::NoError
                                                          ? tr("HTTP %1").arg(statusCode)
                                                          : _reply->errorString());
    _reply->deleteLater();
    _reply = nullptr;

    if (success) {
        _lastTelemetryUtc = QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
    }
    _setConnectionState(success, errorText);
}

void DispatcherClient::_sendEvent(const QJsonObject& event)
{
    if (!_enabled) {
        return;
    }

    const QUrl endpoint(_serverUrl + QStringLiteral("/api/v1/events"));
    if (!endpoint.isValid() || (endpoint.scheme() != QStringLiteral("http")
                                && endpoint.scheme() != QStringLiteral("https"))) {
        return;
    }

    QNetworkRequest request(endpoint);
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    request.setHeader(QNetworkRequest::UserAgentHeader, QStringLiteral("FlyCam-Drone-Control-Center/1.0"));
    request.setTransferTimeout(REQUEST_TIMEOUT_MS);
    if (!_apiKey.isEmpty()) {
        request.setRawHeader("X-API-Key", _apiKey.toUtf8());
    }

    QNetworkReply* eventReply = _networkManager.post(request, QJsonDocument(event).toJson(QJsonDocument::Compact));
    (void) connect(eventReply, &QNetworkReply::finished, eventReply, &QNetworkReply::deleteLater);
}

void DispatcherClient::_setConnectionState(bool reachable, const QString& errorText)
{
    if ((_serverReachable == reachable) && (_lastError == errorText)) {
        return;
    }
    _serverReachable = reachable;
    _lastError = errorText;
    emit connectionStateChanged();
}

void DispatcherClient::_loadConfiguration()
{
    QSettings settings;
    settings.beginGroup(QString::fromLatin1(SETTINGS_GROUP));
    _enabled = settings.value(QString::fromLatin1(ENABLED_KEY), false).toBool();
    _serverUrl = settings.value(QString::fromLatin1(SERVER_URL_KEY), _serverUrl).toString();
    _apiKey = settings.value(QString::fromLatin1(API_KEY), QString()).toString();
    settings.endGroup();
}

void DispatcherClient::_saveConfiguration() const
{
    QSettings settings;
    settings.beginGroup(QString::fromLatin1(SETTINGS_GROUP));
    settings.setValue(QString::fromLatin1(ENABLED_KEY), _enabled);
    settings.setValue(QString::fromLatin1(SERVER_URL_KEY), _serverUrl);
    settings.setValue(QString::fromLatin1(API_KEY), _apiKey);
    settings.endGroup();
}

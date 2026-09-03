#pragma once

#include <optional>

#include <QtCore/QJsonObject>
#include <QtCore/QObject>
#include <QtCore/QString>

#include "Vehicle/VehicleTypes.h"

class Vehicle;

class CargoBayController : public QObject
{
    Q_OBJECT

    Q_PROPERTY(bool vehicleAvailable READ vehicleAvailable NOTIFY availabilityChanged)
    Q_PROPERTY(bool px4Vehicle READ px4Vehicle NOTIFY availabilityChanged)
    Q_PROPERTY(bool openAllowed READ openAllowed NOTIFY availabilityChanged)
    Q_PROPERTY(bool closeAllowed READ closeAllowed NOTIFY availabilityChanged)
    Q_PROPERTY(bool commandInProgress READ commandInProgress NOTIFY commandInProgressChanged)
    Q_PROPERTY(bool commandedOpen READ commandedOpen NOTIFY commandedStateChanged)
    Q_PROPERTY(bool commandedStateKnown READ commandedStateKnown NOTIFY commandedStateChanged)
    Q_PROPERTY(bool feedbackAvailable READ feedbackAvailable CONSTANT)
    Q_PROPERTY(QString statusText READ statusText NOTIFY statusChanged)
    Q_PROPERTY(QString logFilePath READ logFilePath CONSTANT)
    Q_PROPERTY(int actuatorNumber READ actuatorNumber WRITE setActuatorNumber NOTIFY configurationChanged)
    Q_PROPERTY(double openValue READ openValue WRITE setOpenValue NOTIFY configurationChanged)
    Q_PROPERTY(double closedValue READ closedValue WRITE setClosedValue NOTIFY configurationChanged)

public:
    explicit CargoBayController(QObject* parent = nullptr);

    static CargoBayController* instance();

    bool vehicleAvailable() const { return _vehicle != nullptr; }
    bool px4Vehicle() const;
    bool openAllowed() const;
    bool closeAllowed() const;
    bool commandInProgress() const { return _commandInProgress; }
    bool commandedOpen() const { return _commandedOpen; }
    bool commandedStateKnown() const { return _commandedStateKnown; }
    bool feedbackAvailable() const { return false; }
    QString statusText() const;
    QString logFilePath() const { return _logFilePath; }

    int actuatorNumber() const { return _actuatorNumber; }
    double openValue() const { return _openValue; }
    double closedValue() const { return _closedValue; }

    void setActuatorNumber(int actuatorNumber);
    void setOpenValue(double openValue);
    void setClosedValue(double closedValue);

    Q_INVOKABLE void requestOpen();
    Q_INVOKABLE void requestClose();
    Q_INVOKABLE void resetConfiguration();

signals:
    void availabilityChanged();
    void commandInProgressChanged();
    void commandedStateChanged();
    void statusChanged();
    void configurationChanged();
    void auditEventRecorded(const QJsonObject& event);

private:
    static void _commandAckEntry(void* resultHandlerData,
                                 int componentId,
                                 const mavlink_command_ack_t& ack,
                                 VehicleTypes::MavCmdResultFailureCode_t failureCode);

    void _setVehicle(Vehicle* vehicle);
    void _refreshAvailability();
    void _sendActuatorCommand(bool open);
    void _handleCommandAck(const mavlink_command_ack_t& ack,
                           VehicleTypes::MavCmdResultFailureCode_t failureCode);
    void _setCommandInProgress(bool commandInProgress);
    void _setStatusMessage(const QString& statusMessage);
    void _loadConfiguration();
    void _saveConfiguration() const;
    void _appendAuditEvent(const QString& eventName,
                           const QString& result,
                           std::optional<double> requestedValue = std::nullopt);

    Vehicle* _vehicle = nullptr;
    bool _commandInProgress = false;
    bool _pendingOpen = false;
    bool _commandedOpen = false;
    bool _commandedStateKnown = false;
    QString _statusMessage;
    QString _logFilePath;
    int _actuatorNumber = 1;
    double _openValue = 1.0;
    double _closedValue = -1.0;
};

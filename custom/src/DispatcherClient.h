#pragma once

#include <QtCore/QJsonObject>
#include <QtCore/QObject>
#include <QtCore/QPointer>
#include <QtCore/QTimer>
#include <QtNetwork/QNetworkAccessManager>
#include <QtQmlIntegration/QtQmlIntegration>

class QNetworkReply;
class Vehicle;

class DispatcherClient final : public QObject
{
    Q_OBJECT
    QML_NAMED_ELEMENT(Dispatcher)
    QML_SINGLETON

    Q_PROPERTY(bool enabled READ enabled WRITE setEnabled NOTIFY configurationChanged)
    Q_PROPERTY(QString serverUrl READ serverUrl WRITE setServerUrl NOTIFY configurationChanged)
    Q_PROPERTY(QString apiKey READ apiKey WRITE setApiKey NOTIFY configurationChanged)
    Q_PROPERTY(bool serverReachable READ serverReachable NOTIFY connectionStateChanged)
    Q_PROPERTY(QString statusText READ statusText NOTIFY connectionStateChanged)
    Q_PROPERTY(QString lastTelemetryUtc READ lastTelemetryUtc NOTIFY connectionStateChanged)

public:
    explicit DispatcherClient(QObject* parent = nullptr);

    static DispatcherClient* instance();

    bool enabled() const { return _enabled; }
    QString serverUrl() const { return _serverUrl; }
    QString apiKey() const { return _apiKey; }
    bool serverReachable() const { return _serverReachable; }
    QString statusText() const;
    QString lastTelemetryUtc() const { return _lastTelemetryUtc; }

    void setEnabled(bool enabled);
    void setServerUrl(const QString& serverUrl);
    void setApiKey(const QString& apiKey);

    Q_INVOKABLE void sendNow();

signals:
    void configurationChanged();
    void connectionStateChanged();

private:
    void _setVehicle(Vehicle* vehicle);
    void _sendTelemetry();
    void _sendEvent(const QJsonObject& event);
    void _handleReply();
    void _setConnectionState(bool reachable, const QString& errorText = QString());
    void _loadConfiguration();
    void _saveConfiguration() const;

    Vehicle* _vehicle = nullptr;
    QTimer _timer;
    QNetworkAccessManager _networkManager;
    QPointer<QNetworkReply> _reply;
    bool _enabled = false;
    bool _serverReachable = false;
    QString _serverUrl = QStringLiteral("http://127.0.0.1:8088");
    QString _apiKey;
    QString _lastError;
    QString _lastTelemetryUtc;
};

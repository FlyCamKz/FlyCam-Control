#include "FlyCamPlugin.h"

#include "CargoBayController.h"
#include "DispatcherClient.h"

#include <QtCore/QApplicationStatic>
#include <QtCore/QFile>
#include <QtGui/QGuiApplication>
#include <QtQml/QQmlApplicationEngine>
#include <QtQml/qqml.h>

Q_APPLICATION_STATIC(FlyCamPlugin, _flyCamPluginInstance);

FlyCamPlugin::FlyCamPlugin(QObject *parent)
    : QGCCorePlugin(parent)
{
    QGuiApplication::setApplicationDisplayName(
        QStringLiteral("Satbayev University / FlyCam — Drone Control Center"));
    qmlRegisterSingletonInstance("FlyCam.Core", 1, 0, "CargoBay", CargoBayController::instance());
    qmlRegisterSingletonInstance("FlyCam.Core", 1, 0, "Dispatcher", DispatcherClient::instance());
}

QGCCorePlugin *FlyCamPlugin::instance()
{
    return _flyCamPluginInstance();
}

QQmlApplicationEngine *FlyCamPlugin::createQmlApplicationEngine(QObject *parent)
{
    _qmlEngine = QGCCorePlugin::createQmlApplicationEngine(parent);
    _urlInterceptor = new FlyCamUrlInterceptor();
    _qmlEngine->addUrlInterceptor(_urlInterceptor);
    return _qmlEngine;
}

void FlyCamPlugin::destroyQmlApplicationEngine(QQmlApplicationEngine *qmlEngine)
{
    if (qmlEngine && (qmlEngine == _qmlEngine)) {
        qmlEngine->removeUrlInterceptor(_urlInterceptor);
        delete _urlInterceptor;
        _urlInterceptor = nullptr;
        _qmlEngine = nullptr;
    }

    QGCCorePlugin::destroyQmlApplicationEngine(qmlEngine);
}

QUrl FlyCamUrlInterceptor::intercept(const QUrl &url, DataType type)
{
    if ((type == QmlFile || type == UrlString) && (url.scheme() == QStringLiteral("qrc"))) {
        const QString customPath = QStringLiteral(":/Custom%1").arg(url.path());
        if (QFile::exists(customPath)) {
            QUrl customUrl;
            customUrl.setScheme(QStringLiteral("qrc"));
            customUrl.setPath('/' + customPath.mid(2));
            return customUrl;
        }
    }

    return url;
}

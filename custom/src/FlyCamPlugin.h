#pragma once

#include <QtQml/QQmlAbstractUrlInterceptor>

#include "QGCCorePlugin.h"

class QQmlApplicationEngine;

class FlyCamUrlInterceptor final : public QQmlAbstractUrlInterceptor
{
public:
    QUrl intercept(const QUrl &url, DataType type) final;
};

class FlyCamPlugin final : public QGCCorePlugin
{
    Q_OBJECT

public:
    explicit FlyCamPlugin(QObject *parent = nullptr);

    static QGCCorePlugin *instance();

    QQmlApplicationEngine *createQmlApplicationEngine(QObject *parent) final;
    void destroyQmlApplicationEngine(QQmlApplicationEngine *qmlEngine) final;

private:
    QQmlApplicationEngine *_qmlEngine = nullptr;
    FlyCamUrlInterceptor *_urlInterceptor = nullptr;
};

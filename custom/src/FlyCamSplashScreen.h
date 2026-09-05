#pragma once

#include <memory>

#include <QtGui/QBackingStore>
#include <QtGui/QWindow>

class QExposeEvent;
class QResizeEvent;

class FlyCamSplashScreen final : public QWindow
{
public:
    explicit FlyCamSplashScreen(QWindow *parent = nullptr);

protected:
    void exposeEvent(QExposeEvent *event) final;
    void resizeEvent(QResizeEvent *event) final;

private:
    void _render();

    std::unique_ptr<QBackingStore> _backingStore;
};

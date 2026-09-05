#include "FlyCamSplashScreen.h"

#include <QtCore/QRect>
#include <QtGui/QExposeEvent>
#include <QtGui/QFont>
#include <QtGui/QGuiApplication>
#include <QtGui/QLinearGradient>
#include <QtGui/QPainter>
#include <QtGui/QPainterPath>
#include <QtGui/QPixmap>
#include <QtGui/QRegion>
#include <QtGui/QResizeEvent>
#include <QtGui/QScreen>

FlyCamSplashScreen::FlyCamSplashScreen(QWindow *parent)
    : QWindow(parent)
{
    setSurfaceType(QSurface::RasterSurface);
    _backingStore = std::make_unique<QBackingStore>(this);
    setFlags(Qt::SplashScreen | Qt::FramelessWindowHint | Qt::WindowStaysOnTopHint);
    setTitle(QStringLiteral("FlyCam AeroScope/AgroScope Drone Control Center"));
    resize(840, 390);

    if (QScreen *screen = QGuiApplication::primaryScreen()) {
        const QRect availableGeometry = screen->availableGeometry();
        setPosition(availableGeometry.center() - QPoint(width() / 2, height() / 2));
    }
}

void FlyCamSplashScreen::exposeEvent(QExposeEvent *event)
{
    Q_UNUSED(event)
    _render();
}

void FlyCamSplashScreen::resizeEvent(QResizeEvent *event)
{
    _backingStore->resize(event->size());
    _render();
}

void FlyCamSplashScreen::_render()
{
    if (!isExposed()) {
        return;
    }

    const QRect windowRect(QPoint(0, 0), size());
    const QRegion dirtyRegion(windowRect);
    _backingStore->resize(size());
    _backingStore->beginPaint(dirtyRegion);

    QPainter painter(_backingStore->paintDevice());
    painter.setRenderHints(QPainter::Antialiasing | QPainter::SmoothPixmapTransform | QPainter::TextAntialiasing);

    QLinearGradient background(windowRect.topLeft(), windowRect.bottomRight());
    background.setColorAt(0.0, QColor(QStringLiteral("#071018")));
    background.setColorAt(1.0, QColor(QStringLiteral("#183143")));
    painter.fillRect(windowRect, background);

    const QRectF cardRect(34.0, 34.0, 772.0, 310.0);
    QPainterPath shadowPath;
    shadowPath.addRoundedRect(cardRect.translated(0.0, 6.0), 22.0, 22.0);
    painter.fillPath(shadowPath, QColor(0, 0, 0, 70));

    QPainterPath cardPath;
    cardPath.addRoundedRect(cardRect, 22.0, 22.0);
    painter.fillPath(cardPath, QColor(QStringLiteral("#F7F9FB")));
    painter.setPen(QPen(QColor(QStringLiteral("#3D5668")), 1.0));
    painter.drawPath(cardPath);

    const QPixmap logo(QStringLiteral(":/flycam/branding/flycam_logo.png"));
    if (!logo.isNull()) {
        QSize logoSize = logo.size();
        logoSize.scale(QSize(275, 118), Qt::KeepAspectRatio);
        const QRect logoRect(QPoint(75 + ((275 - logoSize.width()) / 2), 130 + ((118 - logoSize.height()) / 2)),
                             logoSize);
        painter.drawPixmap(logoRect, logo);
    }

    painter.setPen(QColor(QStringLiteral("#CAD3DA")));
    painter.drawLine(QPointF(377.0, 86.0), QPointF(377.0, 292.0));

    QFont font = painter.font();
    font.setPixelSize(48);
    font.setWeight(QFont::DemiBold);
    font.setLetterSpacing(QFont::AbsoluteSpacing, 0.5);
    painter.setFont(font);
    painter.setPen(QColor(QStringLiteral("#11181D")));
    painter.drawText(QRectF(420.0, 83.0, 345.0, 65.0), Qt::AlignLeft | Qt::AlignVCenter, QStringLiteral("FlyCam"));

    font.setPixelSize(24);
    font.setWeight(QFont::DemiBold);
    font.setLetterSpacing(QFont::AbsoluteSpacing, 0.0);
    painter.setFont(font);
    painter.setPen(QColor(QStringLiteral("#1D7F9D")));
    painter.drawText(QRectF(420.0, 151.0, 345.0, 38.0), Qt::AlignLeft | Qt::AlignVCenter,
                     QStringLiteral("AeroScope / AgroScope"));

    font.setPixelSize(18);
    font.setWeight(QFont::Medium);
    font.setLetterSpacing(QFont::AbsoluteSpacing, 2.0);
    painter.setFont(font);
    painter.setPen(QColor(QStringLiteral("#354550")));
    painter.drawText(QRectF(420.0, 196.0, 345.0, 32.0), Qt::AlignLeft | Qt::AlignVCenter,
                     QStringLiteral("DRONE CONTROL CENTER"));

    painter.fillRect(QRectF(420.0, 244.0, 310.0, 2.0), QColor(QStringLiteral("#1D7F9D")));

    font.setPixelSize(13);
    font.setWeight(QFont::Normal);
    font.setLetterSpacing(QFont::AbsoluteSpacing, 0.0);
    painter.setFont(font);
    painter.setPen(QColor(QStringLiteral("#667985")));
    painter.drawText(QRectF(420.0, 259.0, 345.0, 42.0), Qt::AlignLeft | Qt::AlignTop | Qt::TextWordWrap,
                     QStringLiteral("Multi-vehicle control  •  Telemetry  •  Analytics"));

    font.setPixelSize(10);
    font.setWeight(QFont::Normal);
    font.setLetterSpacing(QFont::AbsoluteSpacing, 1.8);
    painter.setFont(font);
    painter.setPen(QColor(QStringLiteral("#A9BCC9")));
    painter.drawText(QRectF(0.0, 360.0, width(), 20.0), Qt::AlignCenter,
                     QStringLiteral("FLYCAM AEROSCOPE/AGROSCOPE"));

    painter.end();
    _backingStore->endPaint();
    _backingStore->flush(dirtyRegion);
}

#pragma once
#include <QColor>
#include <QImage>
#include <QJsonObject>
#include <QString>

namespace overlay {
constexpr int WIDTH = 360;
constexpr int HEIGHT = 520;
constexpr int COMPACT_HEIGHT = 224;
constexpr int VIDEO_HEIGHT = 398;
struct Options {
    QString font = "Sans";
    QColor accent = QColor("#ff657c");
    int opacity = 94;
    bool artwork = true;
    bool video = false;
    bool progress = true;
    bool timestamps = true;
    bool paused = false;
    bool animated = true;
    bool adaptive = true;
    bool briefly = false;
    double duration = .85;
    double displaySeconds = 12;
};
QColor artworkAccent(const QImage &image, QColor fallback);
QString timeLabel(double seconds);

// Time is supplied by the host so transitions can be tested deterministically.
class Card {
public:
    QImage frame(const QJsonObject &state, const QImage &art, bool connected,
                 const Options &options, double now, const QImage &video = {}, const QString &videoKey = {});
    bool displayed() const { return lastDisplay; }
    void show();
    void preview(double now);
    bool previewing(double now) const { return now < previewUntil; }
private:
    struct Visual {
        QJsonObject state;
        QImage art;
        QImage video;
        QColor accent;
        QString key;
        QString signature;
    } current, previous;
    bool visible = false;
    bool lastDisplay = false;
    bool reset = true;
    bool changingTrack = false;
    double entered = 0;
    double changed = -100;
    double lastReady = -100;
    double previewUntil = -100;
    double pendingSince = -100;
    QString pendingKey;
    QImage paint(const Visual &, const Options &, double titleAge, double entrance) const;
};
}

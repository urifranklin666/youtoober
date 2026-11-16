#pragma once
#include <QColor>
#include <QImage>
#include <QJsonObject>
#include <QString>

namespace overlay {
constexpr int WIDTH = 360;
constexpr int HEIGHT = 520;
constexpr int COMPACT_HEIGHT = 224;
struct Options {
    QString font = "Sans";
    QColor accent = QColor("#ff657c");
    int opacity = 94;
    bool artwork = true;
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
                 const Options &options, double now);
    void show();
    void preview(double now);
    bool previewing(double now) const { return now < previewUntil; }
private:
    struct Visual {
        QJsonObject state;
        QImage art;
        QColor accent;
        QString key;
        QString signature;
    } current, previous;
    bool visible = false;
    bool reset = true;
    double entered = 0;
    double changed = -100;
    double lastReady = -100;
    double previewUntil = -100;
    double pendingSince = -100;
    QString pendingKey;
    QImage paint(const Visual &, const Options &, double titleAge, double entrance) const;
};
}

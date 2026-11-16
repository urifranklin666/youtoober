#include <obs-module.h>
#include <obs-frontend-api.h>
#include <graphics/graphics.h>
#include <QApplication>
#include <QWidget>
#include <QLabel>
#include <QPushButton>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QTimer>
#include <QLocalSocket>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QPainter>
#include <QPainterPath>
#include <QLinearGradient>
#include <cmath>
#include <QStandardPaths>
#include <QListWidget>
#include <QSlider>
#include <QProcess>
#include <QDir>
#include <QComboBox>
#include <QElapsedTimer>
#include <QFutureWatcher>
#include <QtConcurrent/QtConcurrentRun>
#include <unistd.h>
#include <mutex>

OBS_DECLARE_MODULE()
MODULE_EXPORT const char *obs_module_description(void) { return "YouTube Music terminal player: native overlay and shared playback controls"; }

struct Shared {
    QJsonObject state;
    QImage artwork;
    bool connected = false;
};
static std::mutex guard;
static Shared shared;
static QWidget *dock = nullptr;
static QLocalSocket *subscription = nullptr;
static QTimer *timer = nullptr;
static QLabel *statusLabel = nullptr;
static QLabel *trackLabel = nullptr;
static QListWidget *queueWidget = nullptr;
static QSlider *seekSlider = nullptr;
static QSlider *volumeSlider = nullptr;
static QComboBox *repeatCombo = nullptr;
static QJsonArray displayedQueue;
static QString artPath;
static QElapsedTimer lastUpdate;
static bool stopping = false;

static QString socketPath() {
    return QStandardPaths::writableLocation(QStandardPaths::RuntimeLocation) + "/ytmusic-cli-" + QString::number(getuid()) + ".sock";
}
static void setDisconnected() {
    std::lock_guard<std::mutex> lock(guard);
    shared.connected = false;
    shared.state = {};
    shared.artwork = {};
}
static void command(const QString &method, QJsonObject params = {}) {
    auto *socket = new QLocalSocket(dock);
    auto *timeout = new QTimer(socket);
    timeout->setSingleShot(true);
    QObject::connect(timeout, &QTimer::timeout, socket, &QObject::deleteLater);
    timeout->start(5000);
    QObject::connect(socket, &QLocalSocket::connected, socket, [socket, method, params]() {
        socket->write(QJsonDocument(QJsonObject{{"version",1},{"id",1},{"method",method},{"params",params}}).toJson(QJsonDocument::Compact) + "\n");
    });
    QObject::connect(socket, &QLocalSocket::readyRead, socket, [socket]() {
        if (!socket->canReadLine()) return;
        auto response = QJsonDocument::fromJson(socket->readLine()).object();
        if (response.contains("error")) statusLabel->setText(response["error"].toObject()["message"].toString());
        socket->deleteLater();
    });
    socket->connectToServer(socketPath());
}
static void acceptState(const QJsonObject &state) {
    lastUpdate.restart();
    {
        std::lock_guard<std::mutex> lock(guard);
        shared.state = state;
        shared.connected = true;
    }
    auto track = state["track"].toObject();
    QString path = track["artwork"].toString();
    if (path != artPath) {
        artPath = path;
        { std::lock_guard<std::mutex> lock(guard); shared.artwork = {}; }
        if (!path.isEmpty()) {
            auto *watcher = new QFutureWatcher<QImage>(dock);
            QObject::connect(watcher, &QFutureWatcher<QImage>::finished, dock, [watcher, path]() {
                if (artPath == path) { std::lock_guard<std::mutex> lock(guard); shared.artwork = watcher->result(); }
                watcher->deleteLater();
            });
            watcher->setFuture(QtConcurrent::run([path](){ return QImage(path).scaled(560,560,Qt::KeepAspectRatioByExpanding,Qt::SmoothTransformation); }));
        }
    }
    trackLabel->setText(track["title"].toString("No song selected") + "\n" + track["artist"].toString());
    QString error = state["error"].toString();
    statusLabel->setText(error.isEmpty() ? state["status"].toString() : error);
    auto queue = state["queue"].toArray();
    if (queue != displayedQueue) {
        int selected = queueWidget->currentRow();
        displayedQueue = queue;
        queueWidget->clear();
        for (const auto &item : queue) {
            auto row = item.toObject();
            queueWidget->addItem(row["title"].toString() + " — " + row["artist"].toString());
        }
        queueWidget->setCurrentRow(qMin(selected, queueWidget->count()-1));
    }
    if (!seekSlider->isSliderDown()) {
        seekSlider->setRange(0,qMax(1,int(state["duration"].toDouble())));
        seekSlider->setValue(int(state["position"].toDouble()));
    }
    if (!volumeSlider->isSliderDown()) volumeSlider->setValue(int(state["volume"].toDouble(100)));
    repeatCombo->blockSignals(true);
    repeatCombo->setCurrentIndex(qMax(0, repeatCombo->findData(state["repeat"].toString())));
    repeatCombo->blockSignals(false);
}
static void connectService() {
    if (stopping || subscription->state() != QLocalSocket::UnconnectedState) return;
    subscription->connectToServer(socketPath());
}

struct Source {
    gs_texture_t *texture = nullptr;
    std::mutex mutex;
    QString font = "Sans";
    QColor accent = QColor("#ff657c");
    int opacity = 94;
    bool artwork = true;
    bool progress = true;
    bool paused = false;
    bool animated = true;
    int entranceMs = 850;
    bool visible = false;
    QString trackKey;
    QElapsedTimer entrance;
};
static const char *sourceName(void*) { return "YouTube Music Now Playing"; }
static void update(void *data, obs_data_t *settings) {
    auto *source = static_cast<Source*>(data);
    std::lock_guard<std::mutex> lock(source->mutex);
    source->font = QString::fromUtf8(obs_data_get_string(settings,"font"));
    uint32_t color = uint32_t(obs_data_get_int(settings,"accent"));
    source->accent = QColor(color & 255,(color >> 8) & 255,(color >> 16) & 255);
    source->opacity = int(obs_data_get_int(settings,"opacity"));
    source->artwork = obs_data_get_bool(settings,"artwork");
    source->progress = obs_data_get_bool(settings,"progress");
    source->paused = obs_data_get_bool(settings,"paused");
    source->animated = obs_data_get_bool(settings,"animated");
    source->entranceMs = qBound(300,int(obs_data_get_int(settings,"entrance_ms")),1500);
}
static void defaults(obs_data_t *settings) {
    obs_data_set_default_string(settings,"font","Sans");
    obs_data_set_default_int(settings,"accent",0x7c65ff);
    obs_data_set_default_int(settings,"opacity",94);
    obs_data_set_default_bool(settings,"artwork",true);
    obs_data_set_default_bool(settings,"progress",true);
    obs_data_set_default_bool(settings,"paused",false);
    obs_data_set_default_bool(settings,"animated",true);
    obs_data_set_default_int(settings,"entrance_ms",850);
}
static obs_properties_t *properties(void*) {
    auto *props = obs_properties_create();
    obs_properties_add_text(props,"font","Font family",OBS_TEXT_DEFAULT);
    obs_properties_add_color(props,"accent","Accent color");
    obs_properties_add_int_slider(props,"opacity","Background opacity",0,100,1);
    obs_properties_add_bool(props,"artwork","Show artwork");
    obs_properties_add_bool(props,"progress","Show progress");
    obs_properties_add_bool(props,"paused","Keep visible while paused");
    obs_properties_add_bool(props,"animated","Animate entrance and song changes");
    obs_properties_add_int_slider(props,"entrance_ms","Entrance duration (ms)",300,1500,50);
    return props;
}
static void *create(obs_data_t *settings, obs_source_t*) {
    auto *source = new Source;
    update(source,settings);
    return source;
}
static void destroy(void *data) {
    auto *source = static_cast<Source*>(data);
    if (source->texture) {
        obs_enter_graphics();
        gs_texture_destroy(source->texture);
        obs_leave_graphics();
    }
    delete source;
}
static constexpr uint32_t CARD_WIDTH = 360;
static constexpr uint32_t CARD_HEIGHT = 480;
static uint32_t width(void*) { return CARD_WIDTH; }
static uint32_t height(void *data) {
    auto *source = static_cast<Source*>(data);
    std::lock_guard<std::mutex> lock(source->mutex);
    return source->artwork ? CARD_HEIGHT : 180;
}
static void sourceShown(void *data) {
    auto *source = static_cast<Source*>(data);
    std::lock_guard<std::mutex> lock(source->mutex);
    source->visible = false;
}
static double smooth(double t) {
    t = qBound(0.0,t,1.0);
    return t*t*(3.0-2.0*t);
}
static void render(void *data, gs_effect_t*) {
    Shared snapshot;
    { std::lock_guard<std::mutex> lock(guard); snapshot = shared; }
    auto *source = static_cast<Source*>(data);
    std::lock_guard<std::mutex> lock(source->mutex);
    QString status = snapshot.state["status"].toString();
    if (!snapshot.connected || (status != "Playing" && !(status == "Paused" && source->paused))) {
        source->visible = false;
        return;
    }
    auto track = snapshot.state["track"].toObject();
    if (track.isEmpty()) { source->visible = false; return; }
    const QString key = track["id"].toString(track["videoId"].toString(track["url"].toString(track["title"].toString()+track["artist"].toString())));
    if (!source->visible || source->trackKey != key) {
        source->entrance.start();
        source->trackKey = key;
        source->visible = true;
    }
    const double t = source->animated ? qBound(0.0,double(source->entrance.elapsed())/source->entranceMs,1.0) : 1.0;
    const double ease = 1.0-std::pow(1.0-t,4.0);
    const double fade = smooth(t/0.42);
    const double back = 1.0+1.7*std::pow(t-1.0,3.0)+0.7*std::pow(t-1.0,2.0);
    const double scale = 0.94+0.06*back;
    const uint32_t cardHeight = source->artwork ? CARD_HEIGHT : 180;
    QImage image(CARD_WIDTH,cardHeight,QImage::Format_RGBA8888_Premultiplied);
    image.fill(Qt::transparent);
    QPainter painter(&image);
    painter.setRenderHint(QPainter::Antialiasing);
    painter.setRenderHint(QPainter::SmoothPixmapTransform);
    painter.setOpacity(fade);
    painter.translate(CARD_WIDTH/2.0,cardHeight/2.0+28.0*(1.0-ease));
    painter.scale(scale,scale);
    painter.translate(-double(CARD_WIDTH)/2.0,-double(cardHeight)/2.0);
    painter.setBrush(QColor(22,23,29,int(255*source->opacity/100.0)));
    painter.setPen(QColor(255,255,255,28));
    painter.drawRoundedRect(16,16,CARD_WIDTH-32,cardHeight-32,20,20);
    const int left = 40;
    const int contentWidth = CARD_WIDTH - 80;
    const int infoTop = source->artwork ? 332 : 48;
    if (source->artwork) {
        const QRect tile(left,32,contentWidth,contentWidth);
        QPainterPath clip;
        clip.addRoundedRect(tile,14,14);
        painter.save();
        painter.setClipPath(clip);
        painter.fillRect(tile,source->accent.darker(180));
        if (!snapshot.artwork.isNull()) {
            const auto &art = snapshot.artwork;
            const int side = qMin(art.width(),art.height());
            const QRect crop((art.width()-side)/2,(art.height()-side)/2,side,side);
            painter.drawImage(tile,art,crop);
        } else {
            painter.setPen(Qt::white);
            painter.setFont(QFont(source->font,64));
            painter.drawText(tile,Qt::AlignCenter,QString::fromUtf8("♫"));
        }
        // A single soft light sweep travels over the artwork during the entrance.
        if (t > 0.08 && t < 0.92) {
            const double sweep = (t-0.08)/0.84;
            const double x = left-160+(contentWidth+320)*sweep;
            QLinearGradient light(x-95,32,x+95,32+contentWidth);
            light.setColorAt(0,QColor(255,255,255,0));
            light.setColorAt(0.5,QColor(255,255,255,int(38*std::sin(sweep*3.141592653589793))));
            light.setColorAt(1,QColor(255,255,255,0));
            painter.fillRect(tile,light);
        }
        painter.restore();
    }
    painter.save();
    painter.setOpacity(fade*smooth((t-0.12)/0.55));
    painter.translate(0,10.0*(1.0-smooth((t-0.12)/0.7)));
    painter.setPen(source->accent);
    painter.setFont(QFont(source->font,9,QFont::Bold));
    painter.drawText(left,infoTop,status == "Playing" ? "NOW PLAYING" : "PAUSED");
    painter.setPen(Qt::white); painter.setFont(QFont(source->font,16,QFont::Bold));
    painter.drawText(QRect(left,infoTop+14,contentWidth,30),Qt::AlignLeft,painter.fontMetrics().elidedText(track["title"].toString(),Qt::ElideRight,contentWidth));
    painter.setFont(QFont(source->font,11)); painter.setPen(QColor("#b5b5c0"));
    painter.drawText(QRect(left,infoTop+54,contentWidth,25),Qt::AlignLeft,painter.fontMetrics().elidedText(track["artist"].toString(),Qt::ElideRight,contentWidth));
    double duration = snapshot.state["duration"].toDouble();
    if (source->progress && duration > 0) {
        painter.setPen(Qt::NoPen); painter.setBrush(QColor(255,255,255,30));
        painter.drawRect(QRectF(left,infoTop+100,contentWidth,3)); painter.setBrush(source->accent);
        painter.drawRect(QRectF(left,infoTop+100,contentWidth*qBound(0.0,snapshot.state["position"].toDouble()/duration,1.0)*smooth((t-0.25)/0.75),3));
    }
    painter.restore();
    painter.end();
    if (source->texture && gs_texture_get_height(source->texture) != cardHeight) {
        gs_texture_destroy(source->texture);
        source->texture = nullptr;
    }
    if (!source->texture) source->texture = gs_texture_create(CARD_WIDTH,cardHeight,GS_RGBA,1,nullptr,GS_DYNAMIC);
    if (!source->texture) return;
    gs_texture_set_image(source->texture,image.constBits(),image.bytesPerLine(),false);
    auto *effect = obs_get_base_effect(OBS_EFFECT_DEFAULT);
    gs_effect_set_texture(gs_effect_get_param_by_name(effect,"image"),source->texture);
    gs_blend_state_push();
    gs_blend_function(GS_BLEND_ONE,GS_BLEND_INVSRCALPHA);
    while (gs_effect_loop(effect,"Draw")) gs_draw_sprite(source->texture,0,CARD_WIDTH,cardHeight);
    gs_blend_state_pop();
}

bool obs_module_load(void) {
    stopping = false;
    obs_source_info info = {};
    info.id = "ytmusic_now_playing"; info.type = OBS_SOURCE_TYPE_INPUT;
    info.output_flags = OBS_SOURCE_VIDEO | OBS_SOURCE_CUSTOM_DRAW; info.get_name = sourceName; info.create = create; info.destroy = destroy;
    info.get_width = width; info.get_height = height; info.video_render = render; info.show = sourceShown;
    info.update = update; info.get_defaults = defaults; info.get_properties = properties;
    obs_register_source(&info);
    dock = new QWidget;
    auto *layout = new QVBoxLayout(dock);
    auto *start = new QPushButton("Start player"); layout->addWidget(start);
    QObject::connect(start,&QPushButton::clicked,dock,[](){
        auto *process = new QProcess(dock);
        QObject::connect(process,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),dock,[process](int code,QProcess::ExitStatus){
            if (code) statusLabel->setText("Cannot start player. Run ytm service start in a terminal.");
            process->deleteLater();
        });
        QString executable = QStandardPaths::findExecutable("ytm");
        if (executable.isEmpty()) executable = QDir::homePath()+"/.local/bin/ytm";
        process->start(executable,{"service","start"});
    });
    trackLabel = new QLabel("No song selected"); trackLabel->setTextFormat(Qt::PlainText); trackLabel->setWordWrap(true); layout->addWidget(trackLabel);
    statusLabel = new QLabel("Disconnected — start the player"); statusLabel->setTextFormat(Qt::PlainText); statusLabel->setWordWrap(true); layout->addWidget(statusLabel);
    auto *transport = new QHBoxLayout; layout->addLayout(transport);
    for (auto method : {"previous","pause","resume","next","stop"}) {
        auto *button = new QPushButton(method); transport->addWidget(button);
        QObject::connect(button,&QPushButton::clicked,dock,[method](){ command(method); });
    }
    layout->addWidget(new QLabel("Position"));
    seekSlider = new QSlider(Qt::Horizontal); layout->addWidget(seekSlider);
    QObject::connect(seekSlider,&QSlider::sliderReleased,dock,[](){command("seek",{{"value",seekSlider->value()}});});
    layout->addWidget(new QLabel("Volume"));
    volumeSlider = new QSlider(Qt::Horizontal); volumeSlider->setRange(0,100); volumeSlider->setValue(100); layout->addWidget(volumeSlider);
    QObject::connect(volumeSlider,&QSlider::sliderReleased,dock,[](){command("volume",{{"value",volumeSlider->value()}});});
    auto *options = new QHBoxLayout; layout->addLayout(options);
    auto *shuffle = new QPushButton("Toggle shuffle"); options->addWidget(shuffle);
    QObject::connect(shuffle,&QPushButton::clicked,dock,[](){command("shuffle");});
    repeatCombo = new QComboBox; options->addWidget(repeatCombo);
    for (auto value : {"off","one","all"}) repeatCombo->addItem(QString("Repeat ")+value,value);
    QObject::connect(repeatCombo,qOverload<int>(&QComboBox::currentIndexChanged),dock,[](int){command("repeat",{{"value",repeatCombo->currentData().toString()}});});
    queueWidget = new QListWidget; layout->addWidget(queueWidget);
    QObject::connect(queueWidget,&QListWidget::itemDoubleClicked,dock,[](QListWidgetItem*){command("select",{{"index",queueWidget->currentRow()}});});
    auto *edits = new QHBoxLayout; layout->addLayout(edits);
    for (auto action : {"Remove","Up","Down","Clear"}) {
        auto *button = new QPushButton(action); edits->addWidget(button);
        QObject::connect(button,&QPushButton::clicked,dock,[action](){
            QString name(action); int row = queueWidget->currentRow();
            if (name == "Clear") command("clear");
            else if (row >= 0 && name == "Remove") command("remove",{{"index",row}});
            else if (row >= 0) {
                int target = row+(name == "Up" ? -1 : 1);
                if (target >= 0 && target < queueWidget->count()) { command("move",{{"index",row},{"target",target}}); queueWidget->setCurrentRow(target); }
            }
        });
    }
    if (!obs_frontend_add_dock_by_id("ytmusic-controls","YouTube Music",dock)) {
        delete dock; dock = nullptr; return false;
    }
    subscription = new QLocalSocket(dock);
    QObject::connect(subscription,&QLocalSocket::connected,dock,[](){subscription->write("{\"version\":1,\"id\":1,\"method\":\"subscribe\"}\n"); lastUpdate.restart();});
    QObject::connect(subscription,&QLocalSocket::disconnected,dock,[](){ setDisconnected(); artPath.clear(); statusLabel->setText("Disconnected — reconnecting…"); });
    QObject::connect(subscription,&QLocalSocket::readyRead,dock,[](){
        while (subscription->canReadLine()) {
            auto message = QJsonDocument::fromJson(subscription->readLine()).object();
            if (message["event"].toString() == "state") acceptState(message["state"].toObject());
        }
    });
    timer = new QTimer(dock);
    QObject::connect(timer,&QTimer::timeout,dock,[](){
        if (subscription->state() == QLocalSocket::ConnectedState && lastUpdate.isValid() && lastUpdate.elapsed() > 3000) {
            subscription->abort(); setDisconnected();
        }
        connectService();
    });
    timer->start(1000); connectService();
    return true;
}
void obs_module_unload(void) {
    stopping = true;
    if (timer) timer->stop();
    if (subscription) { subscription->disconnect(); subscription->abort(); }
    setDisconnected();
    if (dock) obs_frontend_remove_dock("ytmusic-controls");
    dock = nullptr; subscription = nullptr; timer = nullptr;
}

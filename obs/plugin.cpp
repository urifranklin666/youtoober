#include "overlay.hpp"
#include <chrono>
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

static double clockSeconds() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
struct Source {
    gs_texture_t *texture = nullptr;
    std::mutex mutex;
    overlay::Options options;
    overlay::Card card;
};
static const char *sourceName(void*) { return "YouTube Music Now Playing"; }
static void update(void *data, obs_data_t *settings) {
    auto *source = static_cast<Source*>(data);
    std::lock_guard<std::mutex> lock(source->mutex);
    auto &o = source->options;
    o.font = QString::fromUtf8(obs_data_get_string(settings,"font"));
    uint32_t color = uint32_t(obs_data_get_int(settings,"accent"));
    o.accent = QColor(color & 255,(color >> 8) & 255,(color >> 16) & 255);
    o.opacity = int(obs_data_get_int(settings,"opacity"));
    o.artwork = obs_data_get_bool(settings,"artwork");
    o.progress = obs_data_get_bool(settings,"progress");
    o.timestamps = obs_data_get_bool(settings,"timestamps");
    o.paused = obs_data_get_bool(settings,"paused");
    o.animated = obs_data_get_bool(settings,"animated");
    o.adaptive = obs_data_get_bool(settings,"adaptive");
    o.briefly = QString::fromUtf8(obs_data_get_string(settings,"display_mode")) == "brief";
    o.duration = qBound(300,int(obs_data_get_int(settings,"entrance_ms")),1500)/1000.0;
    o.displaySeconds = qBound(5,int(obs_data_get_int(settings,"display_seconds")),60);
    source->card.show();
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
    obs_data_set_default_bool(settings,"adaptive",true);
    obs_data_set_default_bool(settings,"timestamps",true);
    obs_data_set_default_string(settings,"display_mode","always");
    obs_data_set_default_int(settings,"display_seconds",12);
}
static obs_properties_t *properties(void *sourceData) {
    auto *props = obs_properties_create();
    obs_properties_add_text(props,"font","Font family",OBS_TEXT_DEFAULT);
    obs_properties_add_color(props,"accent","Accent color");
    obs_properties_add_int_slider(props,"opacity","Background opacity",0,100,1);
    obs_properties_add_bool(props,"artwork","Show artwork");
    obs_properties_add_bool(props,"progress","Show progress");
    obs_properties_add_bool(props,"paused","Keep visible while paused");
    obs_properties_add_bool(props,"animated","Animate entrance and track crossfades");
    obs_properties_add_int_slider(props,"entrance_ms","Entrance duration (ms)",300,1500,50);
    obs_properties_add_bool(props,"adaptive","Match accent and glow to album artwork");
    obs_properties_add_bool(props,"timestamps","Show elapsed and remaining time");
    auto *mode = obs_properties_add_list(props,"display_mode","Display mode",OBS_COMBO_TYPE_LIST,OBS_COMBO_FORMAT_STRING);
    obs_property_list_add_string(mode,"Always visible while playing","always");
    obs_property_list_add_string(mode,"Briefly on each song change","brief");
    obs_properties_add_int_slider(props,"display_seconds","Song-change display duration (seconds)",5,60,1);
    obs_properties_add_button2(props,"preview","Replay entrance / preview",[](obs_properties_t*,obs_property_t*,void *data){
        if (!data) return false;
        auto *source = static_cast<Source*>(data);
        std::lock_guard<std::mutex> lock(source->mutex);
        source->card.preview(clockSeconds());
        return false;
    },sourceData);
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
static uint32_t width(void*) { return overlay::WIDTH; }
static uint32_t height(void *data) {
    auto *source = static_cast<Source*>(data);
    std::lock_guard<std::mutex> lock(source->mutex);
    return source->options.artwork ? overlay::HEIGHT : overlay::COMPACT_HEIGHT;
}
static void sourceShown(void *data) {
    auto *source = static_cast<Source*>(data);
    std::lock_guard<std::mutex> lock(source->mutex);
    source->card.show();
}
static void render(void *data, gs_effect_t*) {
    Shared snapshot;
    { std::lock_guard<std::mutex> lock(guard); snapshot = shared; }
    auto *source = static_cast<Source*>(data);
    std::lock_guard<std::mutex> lock(source->mutex);
    const QImage image = source->card.frame(snapshot.state,snapshot.artwork,snapshot.connected,source->options,clockSeconds());
    const uint32_t cardHeight = image.height();
    if (source->texture && gs_texture_get_height(source->texture) != cardHeight) {
        gs_texture_destroy(source->texture);
        source->texture = nullptr;
    }
    if (!source->texture) source->texture = gs_texture_create(overlay::WIDTH,cardHeight,GS_RGBA,1,nullptr,GS_DYNAMIC);
    if (!source->texture) return;
    gs_texture_set_image(source->texture,image.constBits(),image.bytesPerLine(),false);
    auto *effect = obs_get_base_effect(OBS_EFFECT_DEFAULT);
    gs_effect_set_texture(gs_effect_get_param_by_name(effect,"image"),source->texture);
    gs_blend_state_push();
    gs_blend_function(GS_BLEND_ONE,GS_BLEND_INVSRCALPHA);
    while (gs_effect_loop(effect,"Draw")) gs_draw_sprite(source->texture,0,overlay::WIDTH,cardHeight);
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

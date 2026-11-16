#include <obs.h>
#include <QApplication>
#include <QWidget>
#include <cstdio>
#include <obs-nix-platform.h>
#include <wayland-client.h>
#include <QLocalServer>
#include <QLocalSocket>
#include <QStandardPaths>
#include <QJsonDocument>
#include <QJsonObject>
#include <QThread>
#include <QImage>
#include <unistd.h>
static QWidget *dock = nullptr;
extern "C" bool obs_frontend_add_dock_by_id(const char*,const char*,void *widget) { dock=static_cast<QWidget*>(widget); return true; }
extern "C" void obs_frontend_remove_dock(const char*) { delete dock; dock=nullptr; }
int main(int argc,char **argv) {
    QApplication app(argc,argv);
    if(argc<2) return 1;
    wl_display *display=nullptr;
    if(argc>2) {
        display=wl_display_connect(nullptr);
        if(!display) return 5;
        obs_set_nix_platform(OBS_NIX_PLATFORM_WAYLAND);
        obs_set_nix_platform_display(display);
    }
    if(!obs_startup("en-US",nullptr,nullptr)) return 1;
    QLocalServer server;
    if(argc>2) {
        if(!server.listen(QStandardPaths::writableLocation(QStandardPaths::RuntimeLocation)+"/ytmusic-cli-"+QString::number(getuid())+".sock")) return 6;
        QObject::connect(&server,&QLocalServer::newConnection,&server,[&server](){
            auto *socket=server.nextPendingConnection();
            QObject::connect(socket,&QLocalSocket::readyRead,socket,[socket](){
                socket->readAll();
                QJsonObject track{{"title","Midnight in Motion"},{"artist","The Night Session"}};
                QJsonObject state{{"status","Playing"},{"track",track},{"duration",240},{"position",87},{"volume",65},{"repeat","off"}};
                socket->write(QJsonDocument(QJsonObject{{"event","state"},{"state",state}}).toJson(QJsonDocument::Compact)+"\n");
            });
        });
        obs_video_info video={}; video.graphics_module="libobs-opengl"; video.fps_num=30; video.fps_den=1;
        video.base_width=video.output_width=360; video.base_height=video.output_height=480;
        video.output_format=VIDEO_FORMAT_RGBA; video.colorspace=VIDEO_CS_709; video.range=VIDEO_RANGE_FULL;
        if(obs_reset_video(&video)!=OBS_VIDEO_SUCCESS) return 7;
    }
    obs_module_t *module=nullptr;
    if(obs_open_module(&module,argv[1],".")!=MODULE_SUCCESS || !obs_init_module(module)) return 2;
    auto *settings=obs_data_create();
    auto *source=obs_source_create("ytmusic_now_playing","Smoke test",settings,nullptr);
    if(!source || obs_source_get_width(source)!=360 || obs_source_get_height(source)!=480 || !dock) return 3;
    auto *props=obs_source_properties(source);
    if(!obs_properties_get(props,"paused") || !obs_properties_get(props,"accent")) return 4;
    obs_properties_destroy(props);
    obs_source_update(source,settings);
    obs_source_inc_showing(source);
    app.processEvents();
    if(argc>2) {
        for(int i=0;i<100;i++) { app.processEvents(); QThread::msleep(10); }
        obs_set_output_source(0,source);
        // Render the complete entrance, keeping frames for visual verification.
        for (int frame=0;frame<=12;frame++) {
        app.processEvents();
        obs_enter_graphics();
        auto *target=gs_texrender_create(GS_RGBA,GS_ZS_NONE);
        if(!gs_texrender_begin(target,360,480)) return 8;
        vec4 clear={}; gs_clear(GS_CLEAR_COLOR,&clear,0,0);
        gs_ortho(0,360,0,480,-100,100);
        obs_source_video_render(source);
        gs_texrender_end(target);
        auto *stage=gs_stagesurface_create(360,480,GS_RGBA);
        gs_stage_texture(stage,gs_texrender_get_texture(target));
        uint8_t *pixels=nullptr; uint32_t stride=0;
        if(!gs_stagesurface_map(stage,&pixels,&stride)) return 9;
        QImage image(pixels,360,480,stride,QImage::Format_RGBA8888_Premultiplied);
        QString filename = frame == 12 ? QString(argv[2]) : QString(argv[2])+QString("-%1.png").arg(frame,2,10,QChar('0'));
        bool saved=image.save(filename);
        if (frame == 12) {
            bool visible = false;
            for (int y=0;y<image.height() && !visible;y++)
                for (int x=0;x<image.width();x++)
                    if (image.pixelColor(x,y).alpha() > 0) { visible = true; break; }
            if (!visible) { std::fputs("Final animation frame is empty\n",stderr); return 11; }
        }
        gs_stagesurface_unmap(stage); gs_stagesurface_destroy(stage); gs_texrender_destroy(target);
        obs_leave_graphics();
        if(!saved) return 10;
        QThread::msleep(85);
        }
    }
    obs_set_output_source(0,nullptr);
    obs_source_dec_showing(source);
    obs_source_release(source);
    obs_data_release(settings);
    obs_shutdown();
    if(display) wl_display_disconnect(display);
    puts("Native plugin source, properties, dock lifecycle: OK");
    return 0;
}

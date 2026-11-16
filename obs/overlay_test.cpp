#include "overlay.hpp"
#include <QApplication>
#include <QPainter>
#include <QTextStream>
#include <cstdlib>
static void check(bool value,const char *message) {
    if (!value) { QTextStream(stderr)<<message<<"\n"; std::exit(1); }
}
static bool visible(const QImage &image) {
    for (int y=0;y<image.height();y++) for (int x=0;x<image.width();x++) if (image.pixelColor(x,y).alpha()>0) return true;
    return false;
}
static QJsonObject song(QString id,QString title="Midnight in Motion") {
    return {{"status","Playing"},{"track",QJsonObject{{"id",id},{"title",title},{"artist","The Night Session"}}},{"duration",240},{"position",87}};
}
int main(int argc,char **argv) {
    QApplication app(argc,argv);
    QImage red(280,280,QImage::Format_ARGB32); red.fill(QColor("#bc2844"));
    QImage blue=red.copy(); blue.fill(QColor("#2a46dc"));
    auto accent=overlay::artworkAccent(red,Qt::green);
    check(accent.red()>accent.blue(),"Artwork should choose a red accent");
    QImage gray=red.copy(); gray.fill(Qt::gray);
    check(overlay::artworkAccent(gray,Qt::green)==Qt::green,"Neutral artwork must use fallback accent");
    check(overlay::timeLabel(87)=="1:27" && overlay::timeLabel(3661)=="1:01:01" && overlay::timeLabel(-1)=="0:00","Timestamp formatting");
    overlay::Options options;
    overlay::Card card;
    check(!visible(card.frame(song("one"),red,true,options,0)),"Entrance should begin transparent");
    auto settled=card.frame(song("one"),red,true,options,1);
    check(visible(settled),"Settled card must be visible");
    card.frame(song("two"),blue,true,options,2);
    auto midpoint=card.frame(song("two"),blue,true,options,2.275);
    auto complete=card.frame(song("two"),blue,true,options,2.6);
    auto pixel=midpoint.pixelColor(100,100);
    check(pixel.red()>40 && pixel.blue()>40,"Crossfade midpoint must contain both album covers");
    check(complete.pixelColor(100,100).blue()>complete.pixelColor(100,100).red(),"Crossfade should finish on new artwork");
    auto loading=song("three"); loading["status"]="Loading";
    check(visible(card.frame(loading,{},true,options,3)),"Loading gap should retain the current visual");
    check(!visible(card.frame(loading,{},false,options,3.1)),"Disconnect must hide the overlay immediately");
    overlay::Card briefly;
    options.briefly=true; options.displaySeconds=5;
    briefly.frame(song("one"),red,true,options,0);
    check(!visible(briefly.frame(song("one"),red,true,options,6)),"Brief mode must expire without reappearing");
    check(!visible(briefly.frame(song("one"),red,true,options,7)),"Periodic state updates must not reset brief mode");
    briefly.frame(song("two"),blue,true,options,8);
    check(visible(briefly.frame(song("two"),blue,true,options,9)),"New song must reopen brief mode");
    briefly.preview(20);
    briefly.frame({}, {}, false,options,20);
    check(visible(briefly.frame({}, {}, false,options,21)),"Preview must work without playback or a service");
    check(!visible(briefly.frame({}, {}, false,options,29)),"Preview must expire");
    options.briefly=false; options.animated=false;
    overlay::Card titles;
    QString longTitle="A very long song title with multiple movements and a featured orchestra, extended live performance recorded at the midnight concert hall";
    auto first=titles.frame(song("long",longTitle),red,true,options,0);
    auto scrolled=titles.frame(song("long",longTitle),red,true,options,6);
    check(first!=scrolled,"Overflowing titles must scroll after a reading pause");
    auto shortFirst=titles.frame(song("short"),red,true,options,10);
    auto shortLater=titles.frame(song("short"),red,true,options,16);
    check(shortFirst==shortLater,"Short titles must remain still");
    options.artwork=false;
    check(titles.frame(song("short"),{},true,options,17).height()==overlay::COMPACT_HEIGHT,"Compact layout height");
    options.artwork=true; options.video=true; options.animated=false;
    QImage videoFrame(640,360,QImage::Format_RGB32); videoFrame.fill(Qt::green);
    { QPainter p(&videoFrame); p.fillRect(320,0,320,360,Qt::blue); }
    overlay::Card videoCard;
    auto withVideo=videoCard.frame(song("video"),red,true,options,0,videoFrame,"video");
    check(withVideo.height()==overlay::VIDEO_HEIGHT,"Video mode should use the landscape layout");
    check(withVideo.pixelColor(50,100).green()>200 && withVideo.pixelColor(310,100).blue()>200,"Video must preserve both edges of the 16:9 picture");
    auto stale=videoCard.frame(song("video"),red,true,options,1,videoFrame,"wrong-track");
    check(stale.pixelColor(180,100).red()>stale.pixelColor(180,100).green(),"Stale video from another track must fall back to artwork");
    QImage nextFrame=videoFrame.copy(); nextFrame.fill(Qt::cyan);
    videoCard.frame(song("video"),red,true,options,2,videoFrame,"video");
    options.animated=true;
    auto moving=videoCard.frame(song("video"),red,true,options,3,nextFrame,"video");
    check(moving.pixelColor(50,100).blue()>200,"Ordinary video frames must not restart artwork crossfades");
    options.video=false;
    if (argc>1) {
        options.artwork=true; options.animated=true;
        // A code-drawn fixture exercises the real artwork crop, palette and framing.
        QImage cover(560,560,QImage::Format_ARGB32_Premultiplied);
        QPainter p(&cover); QLinearGradient gradient(0,0,560,560);
        gradient.setColorAt(0,QColor("#432577")); gradient.setColorAt(1,QColor("#ee7977")); p.fillRect(cover.rect(),gradient);
        p.setPen(QPen(QColor(255,220,200,110),5));
        for (int ring=0;ring<9;ring++) p.drawEllipse(QPointF(280,280),70+ring*22,70+ring*22);
        p.end();
        overlay::Card preview;
        auto track=song("art","Midnight in Motion — A Live Session");
        preview.frame(track,cover,true,options,0);
        for (int i=0;i<=24;i++) preview.frame(track,cover,true,options,i/24.0).save(QString(argv[1])+QString("-%1.png").arg(i,2,10,QChar('0')));
        preview.frame(track,cover,true,options,1.2).save(QString(argv[1])+".png");
        preview.frame(song("next","Blue Hour / The Extended Midnight Orchestra Performance"),blue,true,options,2);
        for (int i=0;i<=24;i++) preview.frame(song("next","Blue Hour / The Extended Midnight Orchestra Performance"),blue,true,options,2+i/24.0).save(QString(argv[1])+QString("-change-%1.png").arg(i,2,10,QChar('0')));
    }
    QTextStream(stdout)<<"Palette, crossfade, timestamps, title scrolling, loading, disconnect, timed display and preview: PASS\n";
}

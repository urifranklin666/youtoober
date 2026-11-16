#include "overlay.hpp"
#include <QFont>
#include <QFontMetricsF>
#include <QLinearGradient>
#include <QPainter>
#include <QPainterPath>
#include <QRadialGradient>
#include <QTextLayout>
#include <QTextOption>
#include <algorithm>
#include <array>
#include <cmath>

namespace overlay {
static double smooth(double value) {
    value = qBound(0.0,value,1.0);
    return value*value*(3-2*value);
}
QColor artworkAccent(const QImage &image, QColor fallback) {
    if (image.isNull()) return fallback;
    struct Bin { double weight=0,red=0,green=0,blue=0; };
    std::array<Bin,16> bins{};
    const auto sample = image.scaled(40,40,Qt::IgnoreAspectRatio,Qt::SmoothTransformation);
    for (int y=0;y<sample.height();y++) for (int x=0;x<sample.width();x++) {
        const auto color = sample.pixelColor(x,y);
        const double saturation = color.hsvSaturationF(), value = color.valueF();
        if (color.alphaF()<.5 || saturation<.18 || value<.12 || color.hsvHueF()<0) continue;
        const double weight = saturation*std::sqrt(value);
        auto &bin = bins[qMin(15,int(color.hsvHueF()*16))];
        bin.weight += weight; bin.red += color.redF()*weight;
        bin.green += color.greenF()*weight; bin.blue += color.blueF()*weight;
    }
    const auto best = std::max_element(bins.begin(),bins.end(),[](const Bin &a,const Bin &b){return a.weight<b.weight;});
    if (best->weight<2) return fallback;
    QColor average = QColor::fromRgbF(best->red/best->weight,best->green/best->weight,best->blue/best->weight);
    return QColor::fromHslF(average.hslHueF(),qBound(.45,average.hslSaturationF(),.75),.70);
}
QString timeLabel(double seconds) {
    if (!std::isfinite(seconds)) seconds=0;
    const qint64 value = qint64(qBound(0.0,seconds,359999.0));
    if (value>=3600) return QString("%1:%2:%3").arg(value/3600).arg(value/60%60,2,10,QChar('0')).arg(value%60,2,10,QChar('0'));
    return QString("%1:%2").arg(value/60).arg(value%60,2,10,QChar('0'));
}
static QString keyFor(const QJsonObject &track) {
    for (auto name : {"id","videoId","url"}) if (!track[name].toString().isEmpty()) return track[name].toString();
    return track["title"].toString()+QChar(0x1f)+track["artist"].toString();
}
void Card::show() { reset=true; }
void Card::preview(double now) { reset=true; previewUntil=now+8; }

QImage Card::paint(const Visual &visual, const Options &options, double age, double entrance) const {
    const int height = options.artwork ? HEIGHT : COMPACT_HEIGHT;
    QImage result(WIDTH,height,QImage::Format_RGBA8888_Premultiplied);
    result.fill(Qt::transparent);
    QPainter p(&result);
    p.setRenderHints(QPainter::Antialiasing|QPainter::SmoothPixmapTransform);
    const QColor accent = options.adaptive ? visual.accent : options.accent;
    const QRectF body(16,16,WIDTH-32,height-32);
    // Several translucent rings keep the shadow soft without a blur pass per frame.
    for (int ring=10;ring>=1;ring--) {
        QColor shadow=accent; shadow.setAlpha(2);
        p.setPen(QPen(shadow,ring*1.8)); p.setBrush(Qt::NoBrush);
        p.drawRoundedRect(body.adjusted(2,3,-2,0),22,22);
    }
    const int alpha = int(255*qBound(0,options.opacity,100)/100.0);
    QLinearGradient background(0,16,WIDTH,height);
    QColor tint=accent.darker(650); tint.setAlpha(alpha);
    background.setColorAt(0,tint); background.setColorAt(1,QColor(15,17,23,alpha));
    p.setPen(QColor(255,255,255,24)); p.setBrush(background);
    p.drawRoundedRect(body,22,22);
    const int left=40, content=280, info=options.artwork ? 340 : 44;
    if (options.artwork) {
        const QRectF tile(left,32,content,content);
        p.setPen(Qt::NoPen);
        for (int ring=12;ring>=1;ring--) {
            p.setBrush(QColor(0,0,0,3));
            p.drawRoundedRect(tile.adjusted(-ring/2.0,4-ring/3.0,ring/2.0,4+ring/3.0),16,16);
        }
        QPainterPath clip; clip.addRoundedRect(tile,14,14);
        p.save(); p.setClipPath(clip);
        p.fillRect(tile,accent.darker(220));
        if (!visual.art.isNull()) {
            int side=qMin(visual.art.width(),visual.art.height());
            p.drawImage(tile,visual.art,QRect((visual.art.width()-side)/2,(visual.art.height()-side)/2,side,side));
        } else {
            p.setPen(Qt::white); p.setFont(QFont(options.font,64));
            p.drawText(tile,Qt::AlignCenter,QString::fromUtf8("♫"));
        }
        if (entrance>.08 && entrance<.92) {
            double sweep=(entrance-.08)/.84, x=left-160+(content+320)*sweep;
            QLinearGradient light(x-95,32,x+95,312);
            light.setColorAt(0,Qt::transparent);
            light.setColorAt(.5,QColor(255,255,255,int(32*std::sin(sweep*3.141592653589793))));
            light.setColorAt(1,Qt::transparent); p.fillRect(tile,light);
        }
        p.restore();
        QLinearGradient highlight(left,32,left+content,312);
        highlight.setColorAt(0,QColor(255,255,255,85)); highlight.setColorAt(.45,QColor(255,255,255,18));
        highlight.setColorAt(1,QColor(255,255,255,5));
        p.setBrush(Qt::NoBrush); p.setPen(QPen(highlight,1)); p.drawRoundedRect(tile,14,14);
    }
    p.save();
    p.setOpacity(smooth((entrance-.12)/.55));
    p.translate(0,10*(1-smooth((entrance-.12)/.7)));
    const auto track=visual.state["track"].toObject();
    const QString status=visual.state["status"].toString();
    p.setPen(accent); p.setFont(QFont(options.font,9,QFont::Bold));
    p.drawText(left,info,status=="Paused" ? "PAUSED" : status=="Loading" ? "LOADING NEXT SONG" : "NOW PLAYING");

    // QTextLayout preserves Unicode shaping and wraps at word boundaries, including long words.
    QFont titleFont(options.font,16,QFont::DemiBold);
    QTextLayout title(track["title"].toString(),titleFont);
    QTextOption wrap; wrap.setWrapMode(QTextOption::WrapAtWordBoundaryOrAnywhere); title.setTextOption(wrap);
    title.beginLayout();
    double textHeight=0;
    while (true) {
        auto line=title.createLine(); if (!line.isValid()) break;
        line.setLineWidth(content); line.setPosition(QPointF(0,textHeight)); textHeight+=line.height();
    }
    title.endLayout();
    const double viewport=58;
    double scroll=0;
    if (textHeight>viewport) {
        const double travel=textHeight-viewport;
        const double movement=qMax(2.0,travel/13.0);
        const double cycle=4+movement*2;
        const double phase=std::fmod(qMax(0.0,age),cycle);
        if (phase>2 && phase<2+movement) scroll=travel*(phase-2)/movement;
        else if (phase>=2+movement && phase<4+movement) scroll=travel;
        else if (phase>=4+movement) scroll=travel*(1-(phase-4-movement)/movement);
    }
    p.save(); p.setClipRect(QRectF(left,info+14,content,viewport)); p.setPen(Qt::white);
    title.draw(&p,QPointF(left,info+14-scroll)); p.restore();
    p.setFont(QFont(options.font,11)); p.setPen(QColor("#b9bac7"));
    p.drawText(QRect(left,info+78,content,24),Qt::AlignLeft,p.fontMetrics().elidedText(track["artist"].toString(),Qt::ElideRight,content));
    const double duration=qMax(0.0,visual.state["duration"].toDouble());
    const double position=qMax(0.0,visual.state["position"].toDouble());
    if (options.progress && duration>0) {
        p.setPen(Qt::NoPen); p.setBrush(QColor(255,255,255,24)); p.drawRoundedRect(QRectF(left,info+124,content,3),1.5,1.5);
        p.setBrush(accent); p.drawRoundedRect(QRectF(left,info+124,content*qBound(0.0,position/duration,1.0)*smooth((entrance-.25)/.75),3),1.5,1.5);
    }
    if (options.timestamps) {
        p.setPen(QColor("#a8aab9")); p.setFont(QFont(options.font,9));
        p.drawText(QRect(left,info+136,content/2,20),Qt::AlignLeft,timeLabel(duration>0?qMin(position,duration):position));
        p.drawText(QRect(left+content/2,info+136,content/2,20),Qt::AlignRight,duration>0 ? QString::fromUtf8("−")+timeLabel(qMax(0.0,duration-position)) : QString::fromUtf8("—"));
    }
    p.restore();
    return result;
}

QImage Card::frame(const QJsonObject &input, const QImage &art, bool connected, const Options &o, double now) {
    const int height=o.artwork ? HEIGHT : COMPACT_HEIGHT;
    auto blank=[height](){ QImage image(WIDTH,height,QImage::Format_RGBA8888_Premultiplied); image.fill(Qt::transparent); return image; };
    QJsonObject state=input;
    QImage image=art;
    bool preview=previewing(now);
    if (preview) {
        connected=true;
        if (state["track"].toObject().isEmpty()) {
            state=QJsonObject{{"track",QJsonObject{{"id","preview"},{"title","Midnight in Motion"},{"artist","The Night Session"}}},{"duration",240},{"position",87}};
        }
        state["status"]="Playing";
    }
    QString status=state["status"].toString();
    bool ready=connected && (status=="Playing" || (status=="Paused" && o.paused));
    if (!ready) {
        // Keep the old visual through a short loading gap so the next song can crossfade.
        if (connected && status=="Loading" && visible && !current.key.isEmpty() && now-lastReady<15) {
            state=current.state; state["status"]="Loading"; image=current.art;
        } else { visible=false; return blank(); }
    } else {
        lastReady=now;
        const auto key=keyFor(state["track"].toObject());
        if (key!=pendingKey) { pendingKey=key; pendingSince=now; }
        // Give the asynchronous artwork loader a moment before fading to a placeholder.
        if (key!=current.key && !state["track"].toObject()["artwork"].toString().isEmpty() && image.isNull() && now-pendingSince<.7 && visible) {
            state=current.state; state["status"]="Loading"; image=current.art;
        }
    }
    auto track=state["track"].toObject();
    if (track.isEmpty()) { visible=false; return blank(); }
    QString key=keyFor(track);
    QString signature=key+QChar(0x1f)+track["title"].toString()+QChar(0x1f)+track["artist"].toString()+QString::number(image.cacheKey())+QString::number(o.accent.rgba());
    bool newTrack=key!=current.key;
    bool expired=o.briefly && now-entered>=o.displaySeconds+.5 && !preview;
    if (reset || !visible || (newTrack && expired)) {
        entered=now; previous={}; changed=-100; reset=false;
    } else if (signature!=current.signature) {
        previous=current; changed=now;
        if (newTrack) entered=now;
    }
    if (signature!=current.signature) {
        current.art=image; current.key=key; current.signature=signature;
        current.accent=artworkAccent(image,o.accent);
    }
    current.state=state;
    visible=true;
    double entrance=o.animated ? qBound(0.0,(now-entered)/qMax(.1,o.duration),1.0) : 1;
    // Track crossfades should not replay the full slide/scale entrance.
    if (!previous.key.isEmpty()) entrance=1;
    double alpha=1;
    if (o.briefly && !preview) alpha=1-smooth((now-entered-o.displaySeconds)/.5);
    if (alpha<=0) return blank();
    QImage card=paint(current,o,now-entered,entrance);
    double blend=o.animated ? smooth((now-changed)/.55) : 1;
    if (!previous.key.isEmpty() && blend<1) {
        QImage old=paint(previous,o,now-entered,1);
        QPainter composite(&old);
        // Plus blends premultiplied pixels linearly without a dark midpoint.
        composite.setCompositionMode(QPainter::CompositionMode_DestinationIn); composite.fillRect(old.rect(),QColor(0,0,0,int(255*(1-blend))));
        composite.setCompositionMode(QPainter::CompositionMode_Plus); composite.setOpacity(blend); composite.drawImage(0,0,card); composite.end();
        card=old;
    }
    QImage output=blank(); QPainter p(&output);
    double ease=1-std::pow(1-entrance,4), back=1+1.7*std::pow(entrance-1,3)+.7*std::pow(entrance-1,2);
    double scale=.94+.06*back;
    p.setRenderHint(QPainter::SmoothPixmapTransform);
    p.setOpacity(alpha*smooth(entrance/.42));
    p.translate(WIDTH/2.0,height/2.0+28*(1-ease)); p.scale(scale,scale); p.translate(-WIDTH/2.0,-height/2.0);
    p.drawImage(0,0,card);
    return output;
}
}

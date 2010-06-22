#!/usr/bin/env python
# vim: set fileencoding=utf-8 :
from __future__ import with_statement
"""
Author: Jonas Wagner

Play it slowly
Copyright (C) 2009-2010 Jonas Wagner

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import getopt
import mimetypes
import os
import sys

try:
    import json
except ImportError:
    import simplejson as json

import gobject
gobject.threads_init()

import pygst
pygst.require('0.10')

argv = sys.argv
# work around gstreamer parsing sys.argv!
sys.argv = []
import gst
sys.argv = argv

import pygtk
pygtk.require('2.0')
import gtk

# always enable button images
gtk.settings_get_default().set_long_property("gtk-button-images", True, "main")

from playitslowly import mygtk
mygtk.install()

_ = lambda s: s # may be add gettext later

NAME = u"Play it slowly"
VERSION = "1.3"
WEBSITE = "http://29a.ch/playitslowly/"

if sys.platform == "win32":
    CONFIG_PATH = os.path.expanduser("~/playitslowly.json")
else:
    XDG_CONFIG_HOME = os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config"))
    if not os.path.exists(XDG_CONFIG_HOME):
        os.mkdir(XDG_CONFIG_HOME)
    CONFIG_PATH = os.path.join(XDG_CONFIG_HOME, "playitslowly.json")

TIME_FORMAT = gst.Format(gst.FORMAT_TIME)

def in_pathlist(filename, paths = os.environ.get("PATH").split(os.pathsep)):
    return any(os.path.exists(os.path.join(path, filename)) for path in paths)

class Config(dict):
    def __init__(self, path=None):
        dict.__init__(self)
        self.path = path

    def load(self):
        with open(self.path, "rb") as f:
            try:
                data = json.load(f)
            except Exception:
                data = {}
        self.clear()
        self.update(data)

    def save(self):
        with open(self.path, "wb") as f:
            json.dump(self, f)

class Pipeline(gst.Pipeline):
    def __init__(self, sink):
        gst.Pipeline.__init__(self)
        self.playbin = gst.element_factory_make("playbin")
        self.add(self.playbin)

        bin = gst.Bin("speed-bin")
        try:
            self.speedchanger = gst.element_factory_make("pitch")
        except gst.ElementNotFoundError:
            mygtk.show_error(_(u"You need to install the gstreamer soundtouch elements for "
                    "play it slowly to. They are part of gstreamer-plugins-bad. Consult the "
                    "README if you need more information.")).run()
            raise SystemExit()

        bin.add(self.speedchanger)

        self.audiosink = sink

        bin.add(self.audiosink)
        convert = gst.element_factory_make("audioconvert")
        bin.add(convert)
        gst.element_link_many(self.speedchanger, convert)
        gst.element_link_many(convert, self.audiosink)
        sink_pad = gst.GhostPad("sink", self.speedchanger.get_pad("sink"))
        bin.add_pad(sink_pad)
        self.playbin.set_property("audio-sink", bin)
        bus = self.playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)

        self.eos = lambda: None

    def on_message(self, bus, message):
        t = message.type
        if t == gst.MESSAGE_EOS:
            self.eos()
        elif t == gst.MESSAGE_ERROR:
            mygtk.show_error("gstreamer error: %s - %s" % message.parse_error())

    def set_volume(self, volume):
        self.playbin.set_property("volume", volume)

    def set_speed(self, speed):
        self.speedchanger.set_property("tempo", speed)

    def set_pitch(self, pitch):
        self.speedchanger.set_property("pitch", pitch)

    def save_file(self, uri):
        pipeline = gst.Pipeline()

        playbin = gst.element_factory_make("playbin")
        pipeline.add(playbin)
        playbin.set_property("uri", self.playbin.get_property("uri"))

        bin = gst.Bin("speed-bin")

        speedchanger = gst.element_factory_make("pitch")
        speedchanger.set_property("tempo", self.speedchanger.get_property("tempo"))
        speedchanger.set_property("pitch", self.speedchanger.get_property("pitch"))
        bin.add(speedchanger)

        audioconvert = gst.element_factory_make("audioconvert")
        bin.add(audioconvert)

        encoder = gst.element_factory_make("wavenc")
        bin.add(encoder)

        filesink = gst.element_factory_make("filesink")
        bin.add(filesink)
        filesink.set_property("location", uri)

        gst.element_link_many(speedchanger, audioconvert)
        gst.element_link_many(audioconvert, encoder)
        gst.element_link_many(encoder, filesink)

        sink_pad = gst.GhostPad("sink", speedchanger.get_pad("sink"))
        bin.add_pad(sink_pad)
        playbin.set_property("audio-sink", bin)

        bus = playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)

        pipeline.set_state(gst.STATE_PLAYING)

        return (pipeline, playbin)

    def set_file(self, uri):
        self.playbin.set_property("uri", uri)

    def play(self):
        self.set_state(gst.STATE_PLAYING)

    def pause(self):
        self.set_state(gst.STATE_PAUSED)

    def reset(self):
        self.set_state(gst.STATE_READY)

class MainWindow(gtk.Window):
    def __init__(self, sink, config):
        gtk.Window.__init__(self,gtk.WINDOW_TOPLEVEL)

        self.set_title(NAME)

        try:
            self.set_icon(mygtk.iconfactory.get_icon("playitslowly", 128))
        except gobject.GError:
            print "could not load playitslowly icon"

        self.set_default_size(500, 200)
        self.set_border_width(5)

        self.vbox = gtk.VBox()

        self.pipeline = Pipeline(sink)

        self.filedialog = mygtk.FileChooserDialog(None, self, gtk.FILE_CHOOSER_ACTION_OPEN)
        self.filedialog.connect("response", self.filechanged)
        filechooserhbox = gtk.HBox()
        self.filechooser = gtk.FileChooserButton(self.filedialog)
        filechooserhbox.pack_start(self.filechooser, True, True)
        self.recentbutton = gtk.Button(_("Recent"))
        self.recentbutton.connect("clicked", self.show_recent)
        filechooserhbox.pack_end(self.recentbutton, False, False)

        self.speedchooser = mygtk.TextScale(gtk.Adjustment(1.00, 0.10, 4.0, 0.01, 0.01))
        self.speedchooser.scale.connect("value-changed", self.speedchanged)

        self.pitchchooser = mygtk.TextScale(gtk.Adjustment(0.0, -24.0, 24.0, 1.0, 1.0, 1.0))
        self.pitchchooser.scale.connect("value-changed", self.pitchchanged)

        self.positionchooser = mygtk.TextScale(gtk.Adjustment(0.0, 0.0, 100.0))
        self.positionchooser.scale.connect("button-press-event", self.start_seeking)
        self.positionchooser.scale.connect("button-release-event", self.positionchanged)
        self.seeking = False

        self.startchooser = mygtk.TextScale(gtk.Adjustment(0.0, 0, 100.0))
        self.startchooser.scale.connect("button-press-event", self.start_seeking)
        self.startchooser.scale.connect("button-release-event", self.seeked)

        self.endchooser = mygtk.TextScale(gtk.Adjustment(1.0, 0, 100.0, 0.01, 0.01))
        self.endchooser.scale.connect("button-press-event", self.start_seeking)
        self.endchooser.scale.connect("button-release-event", self.seeked)

        self.vbox.pack_start(mygtk.form([
            (_(u"Audio File"), filechooserhbox),
            (_(u"Playback speed (times)"), self.speedchooser),
            (_(u"Playback Pitch (semitones)"), self.pitchchooser),
            (_(u"Position (seconds)"), self.positionchooser),
            (_(u"Start Position (seconds)"), self.startchooser),
            (_(u"End Position (seconds)"), self.endchooser)
        ]), False, False)

        buttonbox = gtk.HButtonBox()
        self.vbox.pack_end(buttonbox, False, False)

        self.play_button = gtk.ToggleButton(gtk.STOCK_MEDIA_PLAY)
        self.play_button.connect("toggled", self.play)
        self.play_button.set_use_stock(True)
        self.play_button.set_sensitive(False)
        buttonbox.pack_start(self.play_button)

        self.back_button = gtk.Button(gtk.STOCK_MEDIA_REWIND)
        self.back_button.connect("clicked", self.back)
        self.back_button.set_use_stock(True)
        self.back_button.set_sensitive(False)
        buttonbox.pack_start(self.back_button)

        self.volume_button = gtk.VolumeButton()
        self.volume_button.set_value(1.0)
        self.volume_button.set_relief(gtk.RELIEF_NORMAL)
        self.volume_button.connect("value-changed", self.volumechanged)
        buttonbox.pack_start(self.volume_button)

        self.save_as_button = gtk.Button(stock=gtk.STOCK_SAVE_AS)
        self.save_as_button.connect("clicked", self.save)
        self.save_as_button.set_sensitive(False)
        buttonbox.pack_start(self.save_as_button)

        button_about = gtk.Button(stock=gtk.STOCK_ABOUT)
        button_about.connect("clicked", self.about)
        buttonbox.pack_end(button_about)

        self.connect("key-release-event", self.key_release)

        self.add(self.vbox)
        self.connect("destroy", gtk.main_quit)

        self.config = config
        self.config_saving = False
        self.load_config()

    def add_recent(self, uri):
        manager = gtk.recent_manager_get_default()
        mime_type = mimetypes.guess_type(uri)[0]
        app_exec = "playitslowly \"%s\"" % uri
        manager.add_full(uri, {
            "app_name": "playitslowly",
            "app_exec": "playitslowly",
            "mime_type": mime_type
        })


    def show_recent(self, sender=None):
        dialog = gtk.RecentChooserDialog(_("Recent Files"), self, None,
                (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                 gtk.STOCK_OPEN, gtk.RESPONSE_OK))

        filter = gtk.RecentFilter()
        filter.set_name("playitslowly")
        filter.add_application("playitslowly")
        dialog.add_filter(filter)

        filter2 = gtk.RecentFilter()
        filter2.set_name(_("All"))
        filter2.add_mime_type("audio/*")
        dialog.add_filter(filter2)

        dialog.set_filter(filter)

        if dialog.run() == gtk.RESPONSE_OK and dialog.get_current_item():
            uri = dialog.get_current_item().get_uri()
            self.filedialog.set_uri(dialog.get_current_item().get_uri())
            self.filechanged(uri=uri)
        dialog.destroy()

    def load_config(self):
        self.config_saving = True # do not save while loading
        lastfile = self.config.get("lastfile")
        if lastfile:
            self.filedialog.set_uri(lastfile)
            self.filechanged(uri=lastfile)
        self.config_saving = False

    def reset_settings(self):
        self.speedchooser.set_value(1.0)
        self.pitchchooser.set_value(0.0)
        self.startchooser.get_adjustment().set_property("upper", 0.0)
        self.startchooser.set_value(0.0)
        self.endchooser.get_adjustment().set_property("upper", 1.0)
        self.endchooser.set_value(1.0)

    def load_file_settings(self, filename):
        self.add_recent(filename)
        if not self.config or not filename in self.config["files"]:
            self.reset_settings()
            return
        settings = self.config["files"][filename]
        self.speedchooser.set_value(settings["speed"])
        self.pitchchooser.set_value(settings["pitch"])
        self.startchooser.get_adjustment().set_property("upper", settings["duration"])
        self.startchooser.set_value(settings["start"])
        self.endchooser.get_adjustment().set_property("upper", settings["duration"] or 1.0)
        self.endchooser.set_value(settings["end"])
        self.volume_button.set_value(settings["volume"])

    def save_config(self):
        """saves the config file with a delay"""
        if self.config_saving:
            return
        gobject.timeout_add(1000, self.save_config_now)
        self.config_saving = True

    def save_config_now(self):
        self.config_saving = False
        lastfile = self.filedialog.get_uri()
        self.config["lastfile"] = lastfile
        settings = {}
        settings["speed"] = self.speedchooser.get_value()
        settings["pitch"] = self.pitchchooser.get_value()
        settings["duration"] = self.startchooser.get_adjustment().get_property("upper")
        settings["start"] = self.startchooser.get_value()
        settings["end"] = self.endchooser.get_value()
        settings["volume"] = self.volume_button.get_value()
        self.config.setdefault("files", {})[lastfile] = settings

        self.config.save()

    def key_release(self, sender, event):
        if not event.state & gtk.gdk.CONTROL_MASK:
            return
        try:
            val = int(chr(event.keyval))
        except ValueError:
            return
        self.back(self, val)

    def volumechanged(self, sender, foo):
        self.pipeline.set_volume(sender.get_value())
        self.save_config()

    def save(self, sender):
        dialog = mygtk.FileChooserDialog(_(u"Save modified version as"),
                self, gtk.FILE_CHOOSER_ACTION_SAVE)
        dialog.set_current_name("export.wav")
        if dialog.run() == gtk.RESPONSE_OK:
            self.pipeline.set_file(self.filedialog.get_uri())
            self.foo = self.pipeline.save_file(dialog.get_filename())
        dialog.destroy()

    def filechanged(self, sender=None, response_id=gtk.RESPONSE_OK, uri=None):
        if response_id != gtk.RESPONSE_OK:
            return

        self.play_button.set_sensitive(True)
        self.back_button.set_sensitive(True)
        self.save_as_button.set_sensitive(True)
        self.play_button.set_active(False)

        self.pipeline.reset()
        self.seek(0)
        self.save_config()

        if uri:
            self.load_file_settings(uri)
        else:
            # for what ever reason filedialog.get_uri() is sometimes None until the
            # mainloop ran through
            gobject.timeout_add(1, lambda: self.load_file_settings(self.filedialog.get_uri()))

    def pipe_time(self, t):
        """convert from song position to pipeline time"""
        return t/self.speedchooser.get_value()*1000000000

    def song_time(self, t):
        """convert from piletime time to song position"""
        return t*self.speedchooser.get_value()/1000000000

    def start_seeking(self, sender, foo):
        self.seeking = True

    def seeked(self, sender, foo):
        self.seeking = False
        self.save_config()

    def positionchanged(self, sender, foo):
        self.seek(sender.get_value())
        self.seeking = False
        self.save_config()

    def seek(self, pos):
        if self.positionchooser.get_value() != pos:
            print pos
            self.positionchooser.set_value(pos)
        pos = self.pipe_time(pos)
        self.pipeline.playbin.seek_simple(TIME_FORMAT, gst.SEEK_FLAG_FLUSH, pos)

    def speedchanged(self, sender):
        self.pipeline.set_speed(sender.get_value())
        self.save_config()

    def pitchchanged(self, sender):
        self.pipeline.set_pitch(2**(sender.get_value()/12.0))
        self.save_config()

    def back(self, sender, amount=None):
        try:
            position, fmt = self.pipeline.playbin.query_position(TIME_FORMAT, None)
        except gst.QueryError:
            return
        if amount:
            t = self.song_time(position)-amount
            if t < 0:
                t = 0
        else:
            t = 0
        self.seek(t)

    def play(self, sender):
        if sender.get_active():
            self.pipeline.set_file(self.filedialog.get_uri())
            self.pipeline.play()
            gobject.timeout_add(100, self.update_position)
        else:
            self.pipeline.pause()

    def update_position(self):
        """update the position of the scales and pipeline"""
        if self.seeking:
            return self.play_button.get_active()
        try:
            position, fmt = self.pipeline.playbin.query_position(TIME_FORMAT, None)
            duration, fmt = self.pipeline.playbin.query_duration(TIME_FORMAT, None)
        except gst.QueryError:
            return self.play_button.get_active()
        position = self.song_time(position)
        duration = self.song_time(duration)
        start = self.startchooser.get_value()
        end = self.endchooser.get_value()

        if end <= start:
            self.play_button.set_active(False)
            return False

        if position >= end or position < start:
            self.seek(start+0.01)
            return True

        if self.positionchooser.get_adjustment().get_property("upper") != duration:
            self.positionchooser.set_range(0.0, duration)
            self.save_config()
        end = self.endchooser.get_adjustment()
        delta = end.value-end.upper
        if delta <= -duration:
            delta = 0
        self.startchooser.set_range(0.0, duration)
        self.endchooser.set_range(0.0, duration)
        self.endchooser.set_value(duration+delta)
        self.positionchooser.set_value(int(position))
        self.positionchooser.queue_draw()
        return self.play_button.get_active()

    def about(self, sender):
        """show an about dialog"""
        about = gtk.AboutDialog()
        about.set_transient_for(self)
        about.set_logo(mygtk.iconfactory.get_icon("playitslowly", 128))
        about.set_name(NAME)
        about.set_version(VERSION)
        about.set_authors(["Jonas Wagner"])
        about.set_translator_credits(_("translator-credits"))
        about.set_copyright("Copyright (c) 2009-2010 Jonas Wagner")
        about.set_website(WEBSITE)
        about.set_website_label(WEBSITE)
        about.set_license("""
Copyright (C) 2009-2010 Jonas Wagner
This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
""")
        about.run()
        about.destroy()

def main():
    sink = "autoaudiosink"
    if in_pathlist("gstreamer-properties"):
        sink = "gconfaudiosink"
    options, arguments = getopt.getopt(sys.argv[1:], "h", ["help", "sink="])
    for option, argument in options:
        if option in ("-h", "--help"):
            print "Usage: playitslowly [OPTIONS]... [FILE]"
            print "Options:"
            print '--sink=sink      specify gstreamer sink for playback'
            sys.exit()
        elif option == "--sink":
            print "sink", argument
            sink = argument
    config = Config(CONFIG_PATH)
    try:
        config.load()
    except IOError:
        pass
    sink = gst.parse_bin_from_description(sink, True)
    win = MainWindow(sink, config)
    if arguments:
        uri = arguments[0]
        if not uri.startswith("file://"):
            uri = "file://" + os.path.abspath(uri)

        win.filechooser.set_uri(uri)
        win.filechanged(uri=uri)
    win.show_all()
    gtk.main()

if __name__ == "__main__":
    main()

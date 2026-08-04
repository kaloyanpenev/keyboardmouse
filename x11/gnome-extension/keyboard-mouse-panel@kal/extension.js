import Gio from 'gi://Gio';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'org.kal.KeyboardMouse';
const OBJECT_PATH = '/org/kal/KeyboardMouse';

const INTERFACE_XML = `
<node>
  <interface name="${BUS_NAME}">
    <method name="GetState">
      <arg type="b" name="active" direction="out"/>
      <arg type="s" name="color" direction="out"/>
    </method>
    <signal name="StateChanged">
      <arg type="b" name="active"/>
      <arg type="s" name="color"/>
    </signal>
  </interface>
</node>`;

const KeyboardMouseProxy = Gio.DBusProxy.makeProxyWrapper(INTERFACE_XML);

export default class KeyboardMousePanelExtension extends Extension {
    enable() {
        this._proxy = null;
        this._signalId = 0;
        this._cancellable = null;
        this._active = false;
        this._color = null;
        this._originalStyles = new Map();

        // Panels are rebuilt when monitors change, so re-apply onto the new actors.
        this._monitorsChangedId = Main.layoutManager.connect(
            'monitors-changed', () => this._paint());

        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            () => this._onAppeared(),
            () => this._onVanished());
    }

    disable() {
        Main.layoutManager.disconnect(this._monitorsChangedId);
        this._monitorsChangedId = 0;

        this._onVanished();

        Gio.bus_unwatch_name(this._watchId);
        this._watchId = 0;
    }

    _onAppeared() {
        this._cancellable = new Gio.Cancellable();
        new KeyboardMouseProxy(Gio.DBus.session, BUS_NAME, OBJECT_PATH, (proxy, error) => {
            if (error)
                return;

            this._proxy = proxy;
            this._signalId = proxy.connectSignal('StateChanged',
                (sender, name, [active, color]) => this._setState(active, color));
            proxy.GetStateRemote((state, callError) => {
                if (!callError)
                    this._setState(state[0], state[1]);
            });
        }, this._cancellable);
    }

    _onVanished() {
        this._cancellable?.cancel();
        this._cancellable = null;

        if (this._signalId)
            this._proxy.disconnectSignal(this._signalId);
        this._signalId = 0;
        this._proxy = null;

        this._setState(false, null);
    }

    // Every top bar on screen: GNOME's own, plus any a panel-duplicating
    // extension added for other monitors. Both kinds are an actor named 'panel'
    // inside a container named 'panelBox', parented directly to uiGroup by
    // Main.layoutManager.addChrome. Matching on the name rather than the style
    // class matters: GNOME's own panel is styled through its #panel id and
    // carries no 'panel' style class, so a class check would miss it.
    _panels() {
        const panels = [];
        for (const box of Main.uiGroup.get_children()) {
            if (box.name !== 'panelBox')
                continue;
            for (const panel of box.get_children()) {
                if (panel instanceof St.Widget && panel.name === 'panel')
                    panels.push(panel);
            }
        }
        return panels;
    }

    _setState(active, color) {
        this._active = active;
        this._color = color;
        this._paint();
    }

    _paint() {
        const panels = this._panels();

        if (this._active) {
            const style = `background-color: ${this._color};`;
            for (const panel of panels) {
                if (!this._originalStyles.has(panel))
                    this._originalStyles.set(panel, panel.get_style());
                panel.set_style(style);
            }
        } else {
            for (const panel of panels)
                panel.set_style(this._originalStyles.get(panel) ?? null);
            this._originalStyles.clear();
        }
    }
}

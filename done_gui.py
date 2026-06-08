"""Done screen — open the output, show checksums, and test-boot the ISO.

QEMU and VirtualBox each create a throwaway UEFI VM with a 50 GB disk (overwriting
a single reusable test VM) so the installer has a clean target. QEMU can be
installed in-place if missing.
"""

import getpass
import subprocess
from pathlib import Path

import gi

import functions as fn

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

# UEFI firmware images shipped by edk2-ovmf (paths vary by version).
_OVMF_CANDIDATES = (
    "/usr/share/edk2/x64/OVMF.4m.fd",
    "/usr/share/edk2/x64/OVMF.fd",
    "/usr/share/edk2-ovmf/x64/OVMF.fd",
    "/usr/share/OVMF/OVMF.fd",
)

# Self-contained VirtualBox install (adapted from Erik's install script), run as
# root via pkexec. $1 = the target user to add to vboxusers. Installs -headers
# for every kernel present, virtualbox + the DKMS host modules, then loads and
# persists the modules.
_VBOX_INSTALL = (
    'set -uo pipefail\n'
    'echo "== Kernel headers for installed kernels =="\n'
    'declare -A H=( [linux]=linux-headers [linux-lts]=linux-lts-headers'
    ' [linux-zen]=linux-zen-headers [linux-hardened]=linux-hardened-headers'
    ' [linux-lqx]=linux-lqx-headers [linux-cachyos]=linux-cachyos-headers )\n'
    'for k in "${!H[@]}"; do pacman -Qq "$k" &>/dev/null &&'
    ' pacman -S --needed --noconfirm "${H[$k]}"; done\n'
    'echo "== Installing VirtualBox =="\n'
    'pacman -S --needed --noconfirm virtualbox virtualbox-host-dkms\n'
    'echo "== Loading + persisting kernel modules =="\n'
    "printf 'vboxdrv\\nvboxnetadp\\nvboxnetflt\\n' > /etc/modules-load.d/virtualbox.conf\n"
    'modprobe vboxdrv vboxnetadp vboxnetflt || true\n'
    '[[ -n "${1:-}" ]] && { echo "== Adding $1 to vboxusers =="; gpasswd -a "$1" vboxusers || true; }\n'
    'echo "VirtualBox install done."\n'
)


def _out_folder():
    """The ISO output dir (mirrors build-the-iso.sh; shared with functions.py)."""
    return fn.out_folder()


def _latest_iso(folder):
    isos = sorted(folder.glob("*.iso"), key=lambda p: p.stat().st_mtime, reverse=True)
    return isos[0] if isos else None


class DoneScreen:
    def __init__(self, window):
        self.window = window
        self.iso = None

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(self.widget, m)(18)

        title = Gtk.Label(label="Build complete", xalign=0)
        title.add_css_class("screen-title")
        self.widget.append(title)

        self.info = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.widget.append(self.info)

        self.checks = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        self.checks_buf = self.checks.get_buffer()
        scroller = Gtk.ScrolledWindow(min_content_height=140, vexpand=True)
        scroller.set_child(self.checks)
        self.widget.append(scroller)

        bar = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False,
                          min_children_per_line=1, max_children_per_line=8,
                          column_spacing=8, row_spacing=8)
        self.open_btn = Gtk.Button(label="Open output folder")
        self.open_btn.connect("clicked", lambda _w: self._open())
        self.vbox_btn = Gtk.Button(label="Test in VirtualBox")
        self.vbox_btn.connect("clicked", lambda _w: self._test_virtualbox())
        self.vbox_install_btn = Gtk.Button(label="Install VirtualBox")
        self.vbox_install_btn.connect("clicked", lambda _w: self._install_virtualbox())
        self.vm_btn = Gtk.Button(label="Test in QEMU")
        self.vm_btn.connect("clicked", lambda _w: self._test_vm())
        self.install_btn = Gtk.Button(label="Install QEMU")
        self.install_btn.connect("clicked", lambda _w: self._install_qemu())
        self.burn_btn = Gtk.Button(label="Burn to USB")
        self.burn_btn.connect("clicked", lambda _w: self._burn())
        self.burn_install_btn = Gtk.Button(label="Install USB writer")
        self.burn_install_btn.connect("clicked", lambda _w: self._install_mintstick())
        self.log_btn = Gtk.Button(label="Open build log")
        self.log_btn.connect("clicked", lambda _w: self._open_log())
        self.save_profile_btn = Gtk.Button(label="Save build profile…")
        self.save_profile_btn.connect("clicked", lambda _w: self._save_profile())
        again = Gtk.Button(label="Build again")
        again.connect("clicked", lambda _w: self.window.navigate("preflight"))
        bar.append(self.open_btn)
        bar.append(self.vbox_btn)
        bar.append(self.vbox_install_btn)
        bar.append(self.vm_btn)
        bar.append(self.install_btn)
        bar.append(self.burn_btn)
        bar.append(self.burn_install_btn)
        bar.append(self.log_btn)
        bar.append(self.save_profile_btn)
        bar.append(again)
        self.widget.append(bar)

    def on_show(self):
        folder = _out_folder()
        self.checks_buf.set_text("")
        if folder is None or not folder.is_dir():
            self.info.set_text("Output folder not found yet.")
            self._enable(False)
            return
        self.iso = _latest_iso(folder)
        if self.iso is None:
            self.info.set_text(f"No ISO found in {folder}")
            self._enable(False)
            return
        size_gb = self.iso.stat().st_size / 1_000_000_000
        self.info.set_text(f"{self.iso.name}\n{size_gb:.2f} GB  ·  {folder}")
        self._enable(True)
        for ext in ("sha256", "sha1", "md5"):
            f = self.iso.with_name(self.iso.name + "." + ext)
            if f.is_file():
                self.checks_buf.insert(self.checks_buf.get_end_iter(), f.read_text())

    def _enable(self, on):
        self.open_btn.set_sensitive(on)
        self.save_profile_btn.set_sensitive(on)
        self.log_btn.set_sensitive(fn.latest_log_dir() is not None)
        has_vbox = fn.have("VBoxManage")
        self.vbox_btn.set_visible(has_vbox)
        self.vbox_btn.set_sensitive(on and has_vbox)
        self.vbox_install_btn.set_visible(not has_vbox)
        self.vbox_install_btn.set_sensitive(on and not has_vbox)
        has_qemu = fn.have("qemu-system-x86_64")
        # Show "Test in a VM" when QEMU is present, otherwise the install button.
        self.vm_btn.set_visible(has_qemu)
        self.vm_btn.set_sensitive(on and has_qemu)
        self.install_btn.set_visible(not has_qemu)
        self.install_btn.set_sensitive(on and not has_qemu)
        has_mintstick = fn.have("mintstick")
        # Show "Burn to USB" when mintstick is present, otherwise the install button.
        self.burn_btn.set_visible(has_mintstick)
        self.burn_btn.set_sensitive(on and has_mintstick)
        self.burn_install_btn.set_visible(not has_mintstick)
        self.burn_install_btn.set_sensitive(on and not has_mintstick)

    def _open(self):
        folder = _out_folder()
        if folder:
            fn.open_path(folder)

    def _open_log(self):
        log_dir = fn.latest_log_dir()
        if log_dir:
            fn.open_path(log_dir)

    def _test_virtualbox(self):
        if not self.iso or not fn.have("VBoxManage"):
            self._log("VirtualBox (VBoxManage) not installed.")
            return
        self.vbox_btn.set_sensitive(False)
        self._log("Creating a VirtualBox VM (UEFI + 50 GB disk) and booting the ISO…")
        # A throwaway VM, recreated fresh each run so the install disk is clean.
        script = (
            'set -e\n'
            'NAME="kiro-iso-builder-test"\n'
            f'ISO="{self.iso}"\n'
            'VBoxManage controlvm "$NAME" poweroff 2>/dev/null || true\n'
            'VBoxManage unregistervm "$NAME" --delete 2>/dev/null || true\n'
            'VMDIR="$(VBoxManage list systemproperties | sed -n '
            "'s/^Default machine folder: *//p')/$NAME\"\n"
            'VBoxManage createvm --name "$NAME" --ostype ArchLinux_64 --register\n'
            'VBoxManage modifyvm "$NAME" --memory 4096 --vram 128 --cpus 2 '
            '--firmware efi --graphicscontroller vmsvga --boot1 disk --boot2 dvd\n'
            'VBoxManage createmedium disk --filename "$VMDIR/$NAME.vdi" --size 50000\n'
            'VBoxManage storagectl "$NAME" --name SATA --add sata --controller IntelAhci\n'
            'VBoxManage storageattach "$NAME" --storagectl SATA --port 0 --device 0 '
            '--type dvddrive --medium "$ISO"\n'
            'VBoxManage storageattach "$NAME" --storagectl SATA --port 1 --device 0 '
            '--type hdd --medium "$VMDIR/$NAME.vdi"\n'
            'VBoxManage startvm "$NAME" --type gui\n'
        )
        fn.run_pipe(["bash", "-c", script],
                    lambda line: self._log(fn.strip_ansi(line)),
                    self._on_vbox_done)

    def _on_vbox_done(self, code):
        self.vbox_btn.set_sensitive(True)
        self._log("VirtualBox VM started." if code == 0
                  else f"VirtualBox launch failed (exit {code}).")

    def _install_virtualbox(self):
        self.vbox_install_btn.set_sensitive(False)
        self._log("Installing VirtualBox + host DKMS + kernel headers (password required)…")
        fn.run_pipe(
            ["pkexec", "bash", "-c", _VBOX_INSTALL, "kiro-vbox-install", getpass.getuser()],
            lambda line: self._log(fn.strip_ansi(line)),
            self._on_vbox_installed)

    def _on_vbox_installed(self, code):
        if code == 0 and fn.have("VBoxManage"):
            self._log("VirtualBox installed. REBOOT now (a log-out is not enough) to load the "
                      "host kernel modules and join the vboxusers group. Also: enable "
                      "virtualization in your motherboard / BIOS-UEFI settings (Intel VT-x or "
                      "AMD-V / SVM) — VirtualBox cannot run virtual machines without it.")
            self.vbox_install_btn.set_visible(False)
            self.vbox_btn.set_visible(True)
            self.vbox_btn.set_sensitive(True)
        else:
            self._log(f"VirtualBox install failed (exit {code}).")
            self.vbox_install_btn.set_sensitive(True)

    def _test_vm(self):
        if not self.iso or not fn.have("qemu-system-x86_64"):
            return
        cmd = ["qemu-system-x86_64", "-enable-kvm", "-m", "4096"]
        ovmf = next((p for p in _OVMF_CANDIDATES if Path(p).is_file()), None)
        if ovmf:
            cmd += ["-bios", ovmf]   # UEFI boot, not legacy BIOS
        else:
            self._log("OVMF not found (install edk2-ovmf) — booting BIOS instead of UEFI.")
        disk = self._test_disk()
        if disk:
            cmd += ["-drive", f"file={disk},format=qcow2"]   # install target (>=25 GiB)
        # order=cd → hard disk first, CD-ROM fallback: an empty disk boots the ISO
        # installer, but after install the reboot boots the installed system instead
        # of looping back into the ISO.
        cmd += ["-boot", "order=cd", "-cdrom", str(self.iso)]
        subprocess.Popen(cmd)

    def _test_disk(self):
        """A FRESH 50 GB qcow2 each run, so every test starts with a clean target."""
        if not fn.have("qemu-img"):
            self._log("qemu-img not found — the installer will have no disk to install on.")
            return None
        cache = Path.home() / ".cache" / "kiro-iso-builder"
        cache.mkdir(parents=True, exist_ok=True)
        disk = cache / "kiro-test.qcow2"
        if disk.exists():
            disk.unlink()   # overwrite: a clean disk every test (matches VirtualBox)
        self._log("Creating a fresh 50 GB virtual disk for the installer…")
        r = subprocess.run(["qemu-img", "create", "-f", "qcow2", str(disk), "50G"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            self._log(f"Failed to create disk: {r.stderr.strip()}")
            return None
        return disk

    def _install_qemu(self):
        self.install_btn.set_sensitive(False)
        self._log("Installing qemu-desktop (you'll be asked for your password)…")
        fn.run_pipe(
            ["pkexec", "pacman", "-S", "--needed", "--noconfirm", "qemu-desktop"],
            lambda line: self._log(fn.strip_ansi(line)),
            self._on_qemu_installed)

    def _on_qemu_installed(self, code):
        if code == 0 and fn.have("qemu-system-x86_64"):
            self._log("QEMU installed — you can now Test in a VM.")
            self.install_btn.set_visible(False)
            self.vm_btn.set_visible(True)
            self.vm_btn.set_sensitive(True)
        else:
            self._log(f"QEMU install failed (exit {code}).")
            self.install_btn.set_sensitive(True)

    # ── burn the ISO to a USB stick via mintstick ──────────────────────
    def _burn(self):
        if not self.iso or not fn.have("mintstick"):
            return
        # mintstick self-elevates and lets the user pick the target USB device.
        subprocess.Popen(["mintstick", "-m", "iso", "-i", str(self.iso)])

    def _install_mintstick(self):
        self.burn_install_btn.set_sensitive(False)
        self._log("Installing mintstick (you'll be asked for your password)…")
        fn.run_pipe(
            ["pkexec", "pacman", "-S", "--needed", "--noconfirm", "mintstick"],
            lambda line: self._log(fn.strip_ansi(line)),
            self._on_mintstick_installed)

    def _on_mintstick_installed(self, code):
        if code == 0 and fn.have("mintstick"):
            self._log("USB writer installed — you can now Burn to USB.")
            self.burn_install_btn.set_visible(False)
            self.burn_btn.set_visible(True)
            self.burn_btn.set_sensitive(True)
        else:
            self._log(f"USB writer install failed (exit {code}).")
            self.burn_install_btn.set_sensitive(True)

    # ── save a shareable build profile (settings + package selection) ─
    def _save_profile(self):
        kernel = fn.read_conf().get("kernel", "").split()
        suggested = f"kiro-{kernel[0]}{fn.PROFILE_EXT}" if kernel else f"kiro-build{fn.PROFILE_EXT}"
        dialog = Gtk.FileDialog()
        dialog.set_title("Save build profile")
        dialog.set_initial_name(suggested)
        dialog.set_initial_folder(Gio.File.new_for_path(str(fn.profiles_dir())))
        dialog.save(self.window, None, self._on_profile_save_ready)

    def _on_profile_save_ready(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        if not gfile:
            return
        conf = fn.read_conf()
        settings = {k: conf[k] for k in fn.PROFILE_SETTINGS_KEYS if k in conf}
        excludes = fn.read_excludes()
        fn.write_build_profile(gfile.get_path(), settings, excludes)
        self._log(f"Saved build profile ({len(excludes)} package(s) removed) → {gfile.get_path()}")

    def _log(self, line):
        self.checks_buf.insert(self.checks_buf.get_end_iter(), line + "\n")

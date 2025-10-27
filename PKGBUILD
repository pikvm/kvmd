# Contributor: Maxim Devaev <mdevaev@gmail.com>
# Author: Maxim Devaev <mdevaev@gmail.com>


_variants=(
	v0-hdmi:zero2w
	v0-hdmi:rpi2
	v0-hdmi:rpi3

	v0-hdmiusb:zero2w
	v0-hdmiusb:rpi2
	v0-hdmiusb:rpi3

	v1-hdmi:zero2w
	v1-hdmi:rpi2
	v1-hdmi:rpi3

	v1-hdmiusb:zero2w
	v1-hdmiusb:rpi2
	v1-hdmiusb:rpi3

	v2-hdmi:zero2w
	v2-hdmi:rpi3
	v2-hdmi:rpi4

	v2-hdmiusb:rpi4

	v3-hdmi:rpi4

	v4mini-hdmi:rpi4
	v4plus-hdmi:rpi4
)


pkgname=(kvmd)
for _variant in "${_variants[@]}"; do
	_platform=${_variant%:*}
	_board=${_variant#*:}
	pkgname+=(kvmd-platform-$_platform-$_board)
done
pkgbase=kvmd
pkgver=4.110
pkgrel=1
pkgdesc="The main PiKVM daemon"
url="https://github.com/pikvm/kvmd"
license=(GPL)
arch=(any)
depends=(
	"python>=3.13"
	"python<3.14"
	python-yaml
	python-ruamel-yaml
	python-aiohttp
	python-aiofiles
	python-async-lru
	python-passlib
	# python-bcrypt
	python-pyotp
	python-qrcode
	python-periphery
	python-pyserial
	python-pyserial-asyncio
	python-spidev
	python-setproctitle
	python-psutil
	python-netifaces
	python-systemd
	python-dbus
	python-dbus-next
	python-pygments
	"python-pyghmi>=1.6.0-2"
	python-pam
	python-pillow
	python-xlib
	libxkbcommon
	python-hidapi
	python-six
	python-pyrad
	python-ldap
	python-zstandard
	python-mako
	python-luma-oled
	python-pyusb
	python-pyudev
	python-evdev
	"libgpiod>=2.1"
	freetype2
	"v4l-utils>=1.22.1-1"
	"nginx-mainline>=1.25.1"
	openssl
	sudo
	iptables
	iproute2
	dnsmasq
	ipmitool
	"janus-gateway-pikvm>=1.3.0"
	certbot
	"raspberrypi-io-access>=0.7"
	raspberrypi-utils
	"ustreamer>=6.41"

	# Systemd UDEV bug
	"systemd>=248.3-2"

	# https://bugzilla.redhat.com/show_bug.cgi?id=2035802
	# https://archlinuxarm.org/forum/viewtopic.php?f=15&t=15725&start=40
	"zstd>=1.5.1-2.1"

	# Possible hotfix for the new os update
	openssl-1.1

	# Bootconfig
	dos2unix
	parted
	e2fsprogs
	openssh
	# FIXME:
	#   - https://archlinuxarm.org/forum/viewtopic.php?f=15&t=17007&p=72789
	#   - https://github.com/pikvm/pikvm/issues/1375
	wpa_supplicant-pikvm
	run-parts

	# fsck for /boot
	dosfstools

	# pgrep for kvmd-udev-restart-pass, sysctl for kvmd-otgnet
	procps-ng

	# Misc
	hostapd
)
optdepends=(
	tesseract
)
conflicts=(
	python-pikvm
	python-aiohttp-pikvm
	platformio
	avrdude-pikvm
	kvmd-oled

	# See kvmd/crypto.py
	python-bcrypt
)
makedepends=(
	python-setuptools
	python-pip
)
source=("$url/archive/v$pkgver.tar.gz")
md5sums=(SKIP)
backup=(
	etc/kvmd/{override,meta}.yaml
	etc/kvmd/{ht,ipmi,vnc}passwd
	etc/kvmd/totp.secret
	etc/kvmd/nginx/{kvmd.ctx-{http,server},certbot.ctx-server}.conf
	etc/kvmd/nginx/loc-{login,nocache,proxy,websocket,nobuffering,bigpost}.conf
	etc/kvmd/nginx/{mime-types,ssl}.conf
	etc/kvmd/nginx/nginx.conf.mako
	etc/kvmd/janus/janus{,.plugin.ustreamer,.transport.websockets}.jcfg
	etc/kvmd/web.css
)


package_kvmd() {
	install=kvmd.install

	cd "$srcdir/kvmd-$pkgver"
	pip install --root="$pkgdir" --no-deps .

	install -Dm755 -t "$pkgdir/usr/bin" scripts/kvmd-{bootconfig,gencert,certbot}

	install -dm755 "$pkgdir/usr/lib/systemd/system"
	cp -rd configs/os/services -T "$pkgdir/usr/lib/systemd/system"

	install -DTm644 configs/os/sysusers.conf "$pkgdir/usr/lib/sysusers.d/kvmd.conf"
	install -DTm644 configs/os/tmpfiles.conf "$pkgdir/usr/lib/tmpfiles.d/kvmd.conf"

	mkdir -p "$pkgdir/usr/share/kvmd"
	cp -r {switch,hid,web,extras,contrib/keymaps} "$pkgdir/usr/share/kvmd"
	find "$pkgdir/usr/share/kvmd/web" -name '*.pug' -exec rm -f '{}' \;

	local _cfg_default="$pkgdir/usr/share/kvmd/configs.default"
	mkdir -p "$_cfg_default"
	cp -r configs/* "$_cfg_default"

	find "$pkgdir" -name ".gitignore" -delete
	find "$_cfg_default" -type f -exec chmod 444 '{}' \;
	chmod 400 "$_cfg_default/kvmd"/*passwd
	chmod 400 "$_cfg_default/kvmd"/*.secret
	chmod 750 "$_cfg_default/os/sudoers"
	chmod 400 "$_cfg_default/os/sudoers"/*

	mkdir -p "$pkgdir/etc/kvmd/"{nginx,vnc}"/ssl"
	chmod 755 "$pkgdir/etc/kvmd/"{nginx,vnc}"/ssl"
	install -Dm444 -t "$pkgdir/etc/kvmd/nginx" "$_cfg_default/nginx"/*.conf*
	chmod 644 "$pkgdir/etc/kvmd/nginx/"{nginx,ssl}.conf*

	mkdir -p "$pkgdir/etc/kvmd/janus"
	chmod 755 "$pkgdir/etc/kvmd/janus"
	install -Dm444 -t "$pkgdir/etc/kvmd/janus" "$_cfg_default/janus"/*.jcfg

	install -Dm644 -t "$pkgdir/etc/kvmd" "$_cfg_default/kvmd"/*.yaml
	install -Dm600 -t "$pkgdir/etc/kvmd" "$_cfg_default/kvmd"/*passwd
	install -Dm600 -t "$pkgdir/etc/kvmd" "$_cfg_default/kvmd"/*.secret
	install -Dm644 -t "$pkgdir/etc/kvmd" "$_cfg_default/kvmd"/web.css
	mkdir -p "$pkgdir/etc/kvmd/override.d"

	mkdir -p "$pkgdir/var/lib/kvmd/"{msd,pst}
	chmod 1775 "$pkgdir/var/lib/kvmd/pst"
}


for _variant in "${_variants[@]}"; do
	_platform=${_variant%:*}
	_board=${_variant#*:}
	_base=${_platform%-*}
	_video=${_platform#*-}
	eval "package_kvmd-platform-$_platform-$_board() {
		cd \"kvmd-\$pkgver\"

		install=platform.install

		backup=()

		pkgdesc=\"PiKVM platform configs - $_platform for $_board\"
		depends=(kvmd=$pkgver-$pkgrel \"linux-rpi-pikvm>=6.6.45-13\" \"raspberrypi-bootloader-pikvm>=20240818-1\")

		if [[ $_base == v0 ]]; then
			depends=(\"\${depends[@]}\" platformio-core avrdude make patch)
		elif [[ $_base == v4plus ]]; then
			depends=(\"\${depends[@]}\" flashrom-pikvm)
		fi

		if [[ $_platform =~ ^.*-hdmiusb$ ]]; then
			install -Dm755 -t \"\$pkgdir/usr/bin\" scripts/kvmd-udev-hdmiusb-check
		fi
		if [[ $_base == v4plus ]]; then
			install -Dm755 -t \"\$pkgdir/usr/bin\" scripts/kvmd-udev-restart-pass
		fi

		install -DTm644 configs/os/sysctl.conf \"\$pkgdir/usr/lib/sysctl.d/99-kvmd.conf\"
		install -DTm644 configs/os/udev/common.rules \"\$pkgdir/usr/lib/udev/rules.d/99-kvmd-common.rules\"
		install -DTm644 configs/os/udev/$_platform-$_board.rules \"\$pkgdir/usr/lib/udev/rules.d/99-kvmd.rules\"
		install -DTm644 configs/kvmd/main/$_platform-$_board.yaml \"\$pkgdir/usr/lib/kvmd/main.yaml\"

		if [ -f configs/os/modules-load/$_platform.conf ]; then
			install -DTm644 configs/os/modules-load/$_platform.conf \"\$pkgdir/usr/lib/modules-load.d/kvmd.conf\"
		fi

		if [ -f configs/kvmd/fan/$_platform.ini ]; then
			backup=(\"\${backup[@]}\" etc/kvmd/fan.ini)
			depends=(\"\${depends[@]}\" \"kvmd-fan>=0.18\")
			install -DTm444 configs/kvmd/fan/$_platform.ini \"\$pkgdir/etc/kvmd/fan.ini\"
		fi

		if [ -f configs/os/sudoers/$_platform ]; then
			backup=(\"\${backup[@]}\" etc/sudoers.d/99_kvmd)
			install -DTm440 configs/os/sudoers/$_platform \"\$pkgdir/etc/sudoers.d/99_kvmd\"
			chmod 750 \"\$pkgdir/etc/sudoers.d\"
		fi

		if [[ $_platform =~ ^.*-hdmi$ ]]; then
			backup=(\"\${backup[@]}\" etc/kvmd/tc358743-edid.hex etc/kvmd/switch-edid.hex)
			install -DTm444 configs/kvmd/edid/$_base.hex \"\$pkgdir/etc/kvmd/tc358743-edid.hex\"
			ln -s tc358743-edid.hex \"\$pkgdir/etc/kvmd/switch-edid.hex\"
		else
			backup=(\"\${backup[@]}\" etc/kvmd/switch-edid.hex)
			install -DTm444 configs/kvmd/edid/_no-1920x1200.hex \"\$pkgdir/etc/kvmd/switch-edid.hex\"
		fi

		mkdir -p \"\$pkgdir/usr/lib/kvmd\"
		local _platform=\"\$pkgdir/usr/lib/kvmd/platform\"
		rm -f \"\$_platform\"
		echo PIKVM_MODEL=$_base > \"\$_platform\"
		echo PIKVM_VIDEO=$_video >> \"\$_platform\"
		echo PIKVM_BOARD=$_board >> \"\$_platform\"
		chmod 444 \"\$_platform\"
	}"
done

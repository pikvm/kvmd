FROM python:3.9-alpine3.14 as builder
RUN apk add --no-cache \
		alpine-sdk \
		linux-headers \
		libjpeg-turbo-dev \
		libevent-dev \
		libbsd-dev \
		autoconf \
		automake \
		autoconf-archive \
		libtool

COPY patches /patches/
RUN git clone --depth 1 --branch v$(cat /etc/alpine-release) git://git.alpinelinux.org/aports && \
	cd aports/main/musl && \
	abuild -F unpack && \
	sed -i '/pthread_setname_np/a int pthread_getname_np(pthread_t, char *, size_t);' ./src/v1.2.2/include/pthread.h && \
	cp /patches/pthread_getname_np.c src/v1.2.2/src/thread && \
	patch -d src/v1.2.2 -p1 < /patches/getnameinfo.patch && \
	abuild -F build && \
	cd src/v1.2.2 && \
	make install
ARG USTREAMER_MIN_VERSION=4.4
ENV USTREAMER_MIN_VERSION $USTREAMER_MIN_VERSION
RUN echo $USTREAMER_MIN_VERSION
RUN git clone https://github.com/pikvm/ustreamer \
	&& cd ustreamer \
	&& make WITH_PYTHON=1 WITH_OMX=1 PREFIX=/usr DESTDIR=/rootfs install
ARG LIBGPIOD_VERSION=1.6.3
ENV LIBGPIOD_PKG libgpiod-$LIBGPIOD_VERSION
RUN curl \
		-o $LIBGPIOD_PKG.tar.gz \
		https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git/snapshot/$LIBGPIOD_PKG.tar.gz \
	&& tar -xzvf $LIBGPIOD_PKG.tar.gz
RUN cd $LIBGPIOD_PKG \
	&& ./autogen.sh --prefix=/usr --enable-tools=yes --enable-bindings-python \
	&& make PREFIX=/usr DESTDIR=/rootfs install

FROM alpine:3.14
RUN apk add --no-cache \
		py3-pygments \
		py3-aiofiles \
		py3-yaml \
		py3-setproctitle \
		py3-dbus \
		py3-aiohttp \
		py3-xlib \
		py3-pillow \
		iptables \
		bash \
		openssl \
		py3-passlib \
		py3-pyserial \
		nginx \
		libevent

COPY kvmd /kvmd
RUN mkdir -p \
		/etc/kvmd/nginx /etc/kvmd/vnc /etc/kvmd/override.d
COPY configs /usr/share/kvmd/configs.default
# COPY extras /usr/share/kvmd/extras
COPY contrib/keymaps /usr/share/kvmd/keymaps
COPY --from=builder /rootfs /
COPY --from=builder /lib/ld-musl-* /lib/

COPY scripts /scripts
RUN adduser -D kvmd-nginx && \
adduser -D kvmd-vnc && \
/scripts/kvmd-gencert --do-the-thing && \
/scripts/kvmd-gencert --do-the-thing --vnc
#  && \
# chown -R root:root /etc/kvmd/{nginx,vnc}/ssl && \
# chmod 664 /etc/kvmd/{nginx,vnc}/ssl/* && \
# chmod 775 /etc/kvmd/{nginx,vnc}/ssl

# RUN systemd-sysusers /usr/share/kvmd/configs.default/os/sysusers.conf && \
RUN adduser -D kvmd \
		&& adduser -D kvmd-ipmi \
		&& adduser -D kvmd-janus \
		&& mkdir /run/kvmd \
		&& chmod 0775 /run/kvmd \
		&& chown kvmd kvmd /run/kvmd

COPY web /usr/share/kvmd/web

EXPOSE 80
EXPOSE 443

###TODO FIX ME
COPY testenv /testenv
COPY testenv/fakes/vcgencmd /opt/vc/bin/
####

RUN ln -s /sbin/ip /usr/bin/ \
	&& ln -s /sbin/iptables /usr/sbin/ \
	&& adduser -D http \
	&& mkdir -p /fake_sysfs/sys/class/udc/fe980000.usb/device \
	/fake_sysfs/sys/bus/platform/drivers/dwc2 \
	&& echo configured > /fake_sysfs/sys/class/udc/fe980000.usb/state \
	&& ln -s /fake_sysfs/sys/bus/platform/drivers/dwc2 /fake_sysfs/sys/class/udc/fe980000.usb/device/driver && \
	sed -i -e "s/^#PROD//g" /usr/share/kvmd/configs.default/nginx/nginx.conf && \
	mkdir /usr/share/kvmd/extras
ENV KVMD_SYSFS_PREFIX=/fake_sysfs
ENV PLATFORM=v2-hdmi-rpi4
CMD bash -c "\
set -x && \
cp /usr/share/kvmd/configs.default/kvmd/*.yaml /etc/kvmd && \
cp /usr/share/kvmd/configs.default/kvmd/web.css /etc/kvmd && \
cp /usr/share/kvmd/configs.default/kvmd/*passwd /etc/kvmd && \
cp /usr/share/kvmd/configs.default/kvmd/main/${PLATFORM}.yaml /etc/kvmd/main.yaml && \
cp -r /usr/share/kvmd/configs.default/nginx/* /etc/kvmd/nginx && \
nginx -c /etc/kvmd/nginx/nginx.conf -g 'user http; error_log stderr;' && \
python3 -m kvmd.apps.kvmd --run \
"
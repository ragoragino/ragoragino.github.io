ARG ALPINE_VERSION=3.13

FROM alpine:${ALPINE_VERSION}

ARG ZLIB_VERSION=1.2.11

RUN apk update
RUN apk add g++ \
            make \
            cmake \
            ccache \
            python3-dev \
            py-pip \
            git \
            linux-headers \
            nano \
            ctags \
            bash \
            gdb \
            musl-libintl \
            musl-dev \
            file \
            wget \
            go

RUN wget http://www.zlib.net/zlib-${ZLIB_VERSION}.tar.gz && \
    tar xzvf zlib-${ZLIB_VERSION}.tar.gz && \
    cd zlib-${ZLIB_VERSION} && \
    ./configure --prefix=/usr/local && \
    make && \
    make install && \
    cd .. && rm -rf zlib-${ZLIB_VERSION} zlib-${ZLIB_VERSION}.tar.gz
    
SHELL ["/bin/bash", "-c"]

CMD ["bash"]
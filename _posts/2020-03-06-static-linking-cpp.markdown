---
layout: post
title:  "Static Linking of C++ programs"
date:   2021-03-07 13:35:00 +0100
categories: SoftwareEngineering Miscellaneous
---

We always hear about the insecure nature of the majority of container implementations caused by the fact of sharing the kernel with their host. There are countless security recommendations to harden your containers, from running apps as non-roots, restricting the allowed syscall space via a seccomp profile, or running executables in very minimal Linux environments ('scratch' being the best one in the Docker world). 

The last one is easily accomplished for Go executables, which will produce a completely statically-linked executable by defining few environment variables or linker flags during the build process.[^go] However, the situation is different for C++ programs.[^wtf] C++ programs require except a C++ runtime library also a C runtime library. On GNU Linux systems, these are by default libstdc++ and glibc respectively. The catch with glibc is that although producing a fully statically linked executable is possible, the resulting binary will nonetheless require the presence of certain dynamic libraries during the program's runtime.[^glibc]

So, a possible solution to resolve this conundrum is to compile C++ programs against a different C library. There are several C standard libraries, like uClibc-ng, musl libc, or diet libc, that generally target embedded systems and pride themselves on small memory footprints and fully static builds. 

So I have tried to do that with one of the projects in my work that is written in C++. It is a pretty big project that requires linking to several third-party libraries. I have found out that Alpine by default uses musl library, so I have chosen to compile the project in an Alpine container. Musl's libc is a very small C standard library implementation that tries to strictly adhere to the POSIX standard. It is distributed as one static and one shared library (in contrast to glibc being a set of libraries, like libpthread or libm). It allows fully static linking and has a pretty small memory footprint.

I created a simple Dockerfile with Alpine as the base image containing some basic tooling (like g++ compiler, gdb, make, cmake, etc.). As we use some third-party libraries of specific versions (like Boost, Poco, gRPC, etc.), I also had to compile those. To my surprise, this part went pretty smooth and in a short amount of time, I had a working Alpine image with all the necessary C++ libraries successfully installed. However, I started hitting some issues when I wanted to compile the project itself. 

It seems that there are some symbols defined in glibc that are not POSIX-compliant and as I have mentioned, musl's libc tries to adhere strictly to the POSIX standards. I hit several of such cases. Namely, musl does not support transliterations during conversions between character encodings (see [^musl_iconv] and [^musl_iconv_github] for explanation), it also does not provide sys/cdefs.h (which seems to be entirely internal to glibc[^cdefs], but have been used sporadically by outside projects). In addition, musl library is missing some other features that GNU does provide, e.g. I have encountered a compilation failure due to the missing xlocale.h (see LLVM patch[^llvm]). After fixing those issues, the compilation succeeded and I was able to run the static executable in a scratch Docker container.

However, these incompatibilities seem to be relegated to some corner cases and might be only encountered when dealing with a huge codebase (as I did). For smaller, more self-contained projects, compiling with musl's libc should be a safe bet. However, it is definitely useful to check the incompatibilities and open issues page on the musl's libc webpage to make sure there is no serious blocker: <https://wiki.musl-libc.org/functional-differences-from-glibc.html> and <https://wiki.musl-libc.org/open-issues.html/>.

If you would like to see the core of the Alpine Dockerfile that I used to compile a C++ project, head over here: <https://gist.github.com/ragoragino/28affcd44dd2d9021b7da5a42768f98f>. 

A lot of the advice for today's article came from this piece describing a similar replacement of glibc by musl's libc: <https://www.arangodb.com/2018/04/static-binaries-c-plus-plus-application/>.

Some additional resources:
* <https://www.internalpointers.com/post/c-c-standard-library> (a good overview of C/C++ standard libraries)

Footnotes:

[^go]: <https://www.arp242.net/static-go.html>
[^glibc]: <https://www.arangodb.com/2018/04/static-binaries-c-plus-plus-application/> and <https://www.musl-libc.org/intro.html>
[^musl_iconv]: <https://wiki.musl-libc.org/functional-differences-from-glibc.html#iconv>
[^musl_iconv_github]: <https://github.com/akrennmair/newsbeuter/issues/364#issuecomment-250208235>
[^llvm]: <https://reviews.llvm.org/D13673>
[^cdefs]: <https://wiki.musl-libc.org/faq.html#Q:-When-compiling-something-against-musl,-I-get-error-messages-about-%3Ccode%3Esys/cdefs.h%3C/code%3E>
[^wtf]: Some of you might ask, why the heck would I be writing C++ programs in a cloud-native world? In my opinion, for performance-sensitive operations using C++ may be still beneficial in comparison with other cloud-native languages like Go. Also, I would point you to the wonderful Envoy project written entirely in C++. Even though not entirely cloud-native, it is used heavily in cloud environments.
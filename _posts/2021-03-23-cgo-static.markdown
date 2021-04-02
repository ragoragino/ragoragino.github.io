---
layout: post
title:  "Static linking of cgo programs"
date:   2021-03-23 16:55:00 +0100
categories: SoftwareEngineering Miscellaneous
---
In one of my previous articles, I have demonstrated how to statically link C++ programs. I have mentioned that static compilation is quite easy for Golang in comparison to C++.[^static] However, I wanted to try how cgo fares in this respect. cgo handles compilation of C (C++) and Go programs together. It doesn't do that by itself, but it needs an external compiler (and under some circumstances also a linker) to achieve that. Therefore, most of the requirements for fully static linking for C/C++ apply here, i.e. we need to use non-glibc based compilation toolchain (like Alpine's musl) in order to obtain a fully statically linked binary. 

So I have tried statically linking a simple cgo program, that has a C interface exporting a compress function (which itself just calls zlib's compression algorithm). This C part will be compiled as a C static library that Go part (importer.go) will wrap. In the main.go, I will just compress a string by calling the C interface and then decompress it by calling into Go's zlib library. In the end, I just check that the result is equal to the original string.

However, when I wanted to specify flags to be able to link the program statically, I found out that cgo provides two flags for specifying linker options. One, that is passed in the cgo directive (or as an environment variable during go build), called LDFLAGS, and another one, -extldflags, that can be passed on the command line. So this got me wondering, what is really the difference between these two flags, and more generally, how do really cgo compilation and linking work.

I have started the investigation by adding the -x flag to the go build command that prints to the stdout all individual commands that are run under the hood. I have also added the -work flag to force go not to delete intermediate files so I could inspect them after the build process finishes. I have also added the -v flag to LDFLAGS and extldflags to see verbose gcc output. There is also a dense overview of the process on Github's page of cgo command, so I continued to read there when I had some doubts.[^cgo]

As I have found out go's approach to building cgo programs is pretty wild. There are, as with any other build processes, two main stages - compilation and linking. Linking shouldn't be that hard. The result of the compilation will be some object files related to the Go and C source codes, and therefore the role of the linker should be "only" to link the intermediate object files into the final executable. However, modern C compilers use a significant portion of ELF structures and therefore Go team has decided not to go through the pains of implementing a linker that will be able to fully comprehend modern ELF files. They have concluded that there are two main options for how the linkage process could be implemented and the main difference lies in the allowance for dynamic dependencies.  

For programs that can have dynamic dependencies, they resolved the situation by using a little hack. They let the C compiler create an executable (called \_cgo\_.o) that will contain references to all the dynamic libraries that are needed by the C source code. This executable's dynamic sections are then parsed by cgo and passed to the go linker that reuses the list of dynamic symbols and libraries. This linking mode can be specified by the linker command line option with -linkmode set to internal (-ldflags '-linkmode internal'). For example, when I built my compressor C library only statically, using this internal linking resulted in an error:

```console
main(.text): relocation target compressData not defined
```

That's a reasonable error message, as go linker doesn't have any knowledge about the compressData (that originates from the C part of the module) because this function is not present in the list of dynamic symbols needed by the generated C source. However, by changing my compressor library to be built dynamically, this starts to work, as \_cgo\_.o executable created by the gcc contains the dynamic reference to compressData:

```console
bash-5.1# readelf --dyn-sym _cgo_.o

Symbol table '.dynsym' contains 12 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND 
     1: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND compressData
     2: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND malloc
     3: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND __deregister_fra[...]
     4: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND _ITM_registerTMC[...]
     5: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND _ITM_deregisterT[...]
     6: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __libc_start_main
     7: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND __register_frame_info
     8: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND free
     9: 0000000000000000     0 FUNC    WEAK   DEFAULT  UND __cxa_finalize
    10: 0000000000001000     1 FUNC    GLOBAL DEFAULT    7 _init
    11: 0000000000001351     1 FUNC    GLOBAL DEFAULT   11 _fini
```

cgo checks all the dynamic symbols and creates a mirror file (\_cgo\_import.go) that is intended to be reused by go linker.

```console
bash-5.1# cat _cgo_import.go
package main
//go:cgo_import_dynamic compressData compressData ""
//go:cgo_import_dynamic malloc malloc ""
//go:cgo_import_dynamic __deregister_frame_info __deregister_frame_info ""
//go:cgo_import_dynamic _ITM_registerTMCloneTable _ITM_registerTMCloneTable ""
//go:cgo_import_dynamic _ITM_deregisterTMCloneTable _ITM_deregisterTMCloneTable ""
//go:cgo_import_dynamic __libc_start_main __libc_start_main ""
//go:cgo_import_dynamic __register_frame_info __register_frame_info ""
//go:cgo_import_dynamic free free ""
//go:cgo_import_dynamic __cxa_finalize __cxa_finalize ""
//go:cgo_import_dynamic _ _ "libcompressorShared.so"
//go:cgo_import_dynamic _ _ "libc.musl-x86_64.so.1"
```

Go linker just reuses the dynamic section to create the final binary:

```console
bash-5.1# readelf --dyn-sym example

Symbol table '.dynsym' contains 36 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     ...
     4: 0000000000000000     0 OBJECT  GLOBAL DEFAULT  UND malloc
     5: 0000000000000000     0 OBJECT  GLOBAL DEFAULT  UND compressData
     6: 0000000000000000     0 OBJECT  GLOBAL DEFAULT  UND free
     ...
```

The most difficult part of the job is therefore left to the system dynamic linker that resolves the addresses at runtime. This mode has the advantage that external (non-dynamic) linker is not needed after the package is compiled. Cgo authors mention that for example, net package uses libc in the C part and that users do not need to have gcc installed in order to use the package. It is just necessary to have a proper object file compiled for that particular architecture and OS (which is transparently imported as part of Go's standard library in case of net package).

When dynamic dependencies are not available (or cannot be used), then an external linking mode is required. In this case, the linking stage is left to the system linker (like gcc's ld), which already has a complete understanding of the ELF format. Go compiler just creates basic ELF object files from Go source files, so the output of Go build process can be passed to the system linker. However, the translation of dynamic references (\_cgo\_.o to \_cgo\_import.go) is still executed during the external linking stage. The reasoning is succinctly summarized by Go authors: "This conflict between functionality and the gcc requirement means we must support both internal and external linking, depending on the circumstances: if net is the only cgo-using package, then internal linking is probably fine, but if other packages are involved, so that there are dependencies on libraries beyond libc, external linking is likely to work better. The compilation of a package records the relevant information to support both linking modes, leaving the decision to be made when linking the final binary." So Go decides on the fly which linking mode to use. The only exception is when specified explicitly with the linkmode option!

For the compilation part of cgo builds, development and maintenance complexity also persuaded the authors to rely on system tools. The reason is that any C declaration in a Go program would require a full-blown C preprocessor, tokenizer and parser and creating such a beast is absolutely not a trivial task. Therefore, the authors of cgo have decided to try another route. But this is exactly the part where things get a little wild. The cgo compilation process consists of an interplay between cgo and gcc. cgo generates some specially crafted faulty, but syntactically correct, C files that it passes to gcc. gcc tries to compile the files, but is unable to do so, and therefore returns error messages. These error messages are then used by cgo to deduce the kinds of objects defined in the C source code (like if an object is a variable, struct, func, etc.). So cgo uses error messages created by gcc as the official API of gcc. This is pretty wild, no?! Unfortunately, this also makes gcc the only possible compiler for cgo projects!

Continuing further, after cgo learns enough information about the kinds of objects, it creates another C file containing usage of these kinds of objects. This source file is then (this time successfully) compiled by gcc and cgo will parse the DWARF section of the resulting object file in order to learn about the type-specific information of each object kind (like this struct has these fields, and so on). At the end of this stage, cgo will generate some (pure) Go files containing definitions of wrappers of C objects and some C files containing compatibility interface to those Go wrappers. gcc will be then called to separately compile the C sources into object files, and then to link those object files together into an already mentioned \_cgo\_.o executable. 

This is where the first linking stage comes into play and we can see LDFLAGS being already passed to the gcc at this point. This is the gcc invocation as presented by the go build command (with -x flag) for my cgo project:

```console
TERM='dumb' gcc -I . -fPIC -m64 -pthread -fmessage-length=0 -fdebug-prefix-map=$WORK/b001=/tmp/go-build -gno-record-gcc-switches -o $WORK/b001/_cgo_.o $WORK/b001/_cgo_main.o $WORK/b001/_x001.o $WORK/b001/_x002.o $WORK/b001/_x003.o -g -O2 -lcompressor -lz
```

The last '-lcompressor -lz' are exactly flags that I pass to the LDFLAGS in importer.go. \_x001.o, \_x002.o and \_x003.o are object files created from the generated C source code, and \_cgo\_.o is a dummy executable that doesn't actually do nothing (just returns), but serves as a resource for the cgo that uses it to gather dynamic symbols for go linker. That is done by calling cgo with the --dynimport flag which produces \_cgo\_import.go containing all the definitions that will need to be resolved dynamically. 

Go then packs all the Go and C object files together into an archive file, creates files containing references to used Go packages and calls Go linker to create the final binary. Here, either Go continues with internal linking and links all those object files from the archive together by itself reusing all the dynamic dependencies the C compiler detected in previous steps. Or, go just invokes the system linker and the second linking stage starts. Here is the part, where the extldflags are passed together with the LDFLAGS into the system linker to create the final executable. In my case, this is how that gigantic invocation of ld looks like:

```console
/usr/libexec/gcc/x86_64-alpine-linux-musl/10.2.1/collect2 -plugin /usr/libexec/gcc/x86_64-alpine-linux-musl/10.2.1/liblto_plugin.so -plugin-opt=/usr/libexec/gcc/86_64-alpine-linux-musl/10.2.1/lto-wrapper -plugin-opt=-fresolution=/tmp/ccmHdOno.res -plugin-opt=-pass-through=-lgcc -plugin-opt=-pass-through=-lgcc_eh -plugin-opt=-pass-through=-lc --hash-style=gnu -m elf_x86_64 --as-needed -static -z relro -z now -o $WORK/b001/exe/a.out /usr/lib/gcc/x86_64-alpine-linux-musl/10.2.1/../../../../lib/crt1.o /usr/lib/gcc/x86_64-alpine-linux-musl/10.2.1/../../../../lib/crti.o /usr/lib/gcc/x86_64-alpine-linux-musl/10.2.1/crtbeginT.o -L/usr/lib/gcc/x86_64-alpine-linux-musl/10.2.1 -L/usr/lib/gcc/x86_64-alpine-linux-musl/10.2.1/../../../../x86_64-alpine-linux-musl/lib/../lib -L/usr/lib/gcc/x86_64-alpine-linux-musl/10.2.1/../../../../lib -L/lib/../lib -L/usr/lib/../lib -L/usr/lib/gcc/x86_64-alpine-linux-musl/10.2.1/../../../../x86_64-alpine-linux-musl/lib -L/usr/lib/gcc/x86_64-alpine-linux-musl/10.2.1/../../. --compress-debug-sections=zlib-gnu /tmp/go-link-946182175/go.o /tmp/go-link-946182175/000000.o /tmp/go-link-946182175/000001.o /tmp/go-link-946182175/000002.o /tmp/go-link-946182175/000003.o /tmp/go-link-946182175/000004.o /tmp/go-link-946182175/000005.o /tmp/go-link-946182175/000006.o /tmp/go-link-946182175/000007.o /tmp/go-link-946182175/000008.o /tmp/go-link-946182175/000009.o /tmp/go-link-946182175/000010.o /tmp/go-link-946182175/000011.o /tmp/go-link-946182175/000012.o /tmp/go-link-946182175/000013.o /tmp/go-link-946182175/000014.o -lcompressor -lz -lpthread -lssp_nonshared --start-group -lgcc -lgcc_eh -lc --end-group /usr/lib/gcc/x86_64-alpine-linux-musl/10.2.1/crtend.o /usr/lib/gcc/x86_64-alpine-linux-musl/10.2.1/../../../../lib/crtn.o
```

You might have spotted '-lcompressor -lz' and '-static' flags in there. These are exacly the flags passed to gcc from LDFLAGS together with -extldflags. ld then links  all the object files into a final binary, in our case fully statically linked.

That is all for today. If you are interested to check the source files of this demo, you can find them here: <https://github.com/ragoragino/ragoragino.github.io/tree/master/assets/code/cgo-static>

Footnotes:

[^cgo]: <https://github.com/golang/go/blob/860c9c0b8df6c0a2849fdd274a0a9f142cba3ea5/src/cmd/cgo/doc.go>
[^static]: <https://www.arp242.net/static-go.html> and <https://github.com/golang/go/issues/26492>

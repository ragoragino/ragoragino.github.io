In one of my previous articles I have demonstrated how to statically link C++ programs. 'Medzi recou' I have mentioned that static compilation is quite easy for Golang in comparison to C++. However, I wanted to try how cgo fares in this respect. cgo handles compilation of C (C++) and Go programs together. It doesn't do that by itself, but it needs external compiler (and under some circumstances also linker) to achieve that. Therefore, most of the requirements for fully static linking for C/C++ apply here, i.e. we need to use non-glibc based compilation toolchain (like Alpine's musl) in order to obtain a fully statically linked binary. 

So I have tried statically linking a simple cgo program, that has a C interface exporting a compress function (which itself just calls zlib's compression algorithm). This C part will be compiled as a C static library that Go part (importer.go) will wrap. In the main file, I will just compress a string by calling Go's native zlib library and the C interface. At the end, I just check that these two compressed strings are equal. 

After I have written this basic setup, I defined cgo LDFLAGS (flags to be used during linking) inside the C-importing Go file (importer.go) as '-Wl,-static -lcompressor -lz', meaning I want to link it against compressor and zlib library and also the resulting executable should be static. Then I have tried compiling with 'go build -ldflags '-linkmode external -extldflags "-static"' [^static]. This just means that we want to use external linkage (that is invoke gcc's ld) with a static flag. However, the compilation resulted in an error:  

```console
/usr/lib/gcc/x86_64-alpine-linux-musl/10.2.1/../../../../x86_64-alpine-linux-musl/bin/ld: cannot find -lgcc_s
```

After removing the -static flag passed to ld in the LDFLAGS directive (-Wl,-static), the program compiled successfuly.[^google_group_issue] So this got me wondering, what is the difference between -extldflags and LDFLAGS, and more generally, how does really cgo compilation and linking work.

I have started the investigation by adding the -x flag to the go build command that prints to the stdout all individual commands that are run under the hood. I have also added the -work flag to force go not to delete intermediate files so I could inspect them after the build proces finishes. I have also added the -v flag to LDFLAGS and extldflags to see verbose gcc output. There is also a dense overview of the process on Github's page of cgo command, so I continued to read there when I had some doubts.[^cgo]

As I have found out, go's approach to building cgo programs is pretty wild. There are, as with any other build processes, two main stages - compilation and linking. Linking shouldn't be that hard. The result of the compilation will be some object files related to the Go and C source codes, and therefore the role of linker should be "only" to link the intermediate object files into the final executable. However, modern C compilers use a significant portion of ELF structures (object files are also in ELF format), and therefore Go team has decided not to go through the pains of implementing a linker that will be able to fully comprehend modern ELF files. They have concluded that they are two main options how the linkage process could be implemented and the main difference lie in the allowance for dynamic dependencies.  

For programs that can have dynamic dependencies they resolved the situation by using a little hack. They let the C compiler create a executable ('_cgo_.o', we will talk about it more little later) that will contain references to all the dynamic libraries that are needed by the C source code. This executable's dynamic sections are then parsed by go linker that reuses the list of dynamic symbols and libraries. This linking mode can be specified by the linker command line option with -linkmode set to internal (-ldflags '-linkmode internal'). For example, when I built my compressor C library only statically, using this internal linking resulted in an error:

```console
main(.text): relocation target compressData not defined
```

That's a reasonable error message, as go linker doesn't have any knowledge about the compressData because this function is not present in the list of dynamic symbols needed by the generated C source. However, by changing my compressor library to be built dynamically, this starts to work, as '_cgo_.o' object file created by the gcc contains the dynamic reference to compressData:

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

cgo checks all the dynamic symbols and creates a mirror file (_cgo_import.go) that is intended to be reused by go linker.

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

The go linker just reuses the dynamic section to create the final binary:

```console
bash-5.1# readelf --dyn-sym cgo

Symbol table '.dynsym' contains 36 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     ...
     4: 0000000000000000     0 OBJECT  GLOBAL DEFAULT  UND malloc
     5: 0000000000000000     0 OBJECT  GLOBAL DEFAULT  UND compressData
     6: 0000000000000000     0 OBJECT  GLOBAL DEFAULT  UND free
     ...
```

The most difficult part of the job is therefore left to the system dynamic loader. This mode has advantages that external linker is not needed after the package is compiled. The cgo authors mention that for example net package uses libc in the C part and that users do not need to have gcc installed in order to use the package. It is just necessary to have proper object file compiled for that particular architecture and OS (which is transparently imported as part of Go's standard library).

When dynamic dependencies are not available (or cannot be used), then an external linking mode is required. In this case, the linking stage is left to the system linker (like gcc's ld), that already has a complete understanding of the ELF format. Go compiler just creates basic ELF object files from Go source files, so the output of Go build process can be passed to the system linker. However, the dynamic references translation (_cgo_.o to _cgo_import.go) is still needed during the external linking. This is required because Go part will still need to produce a valid ELF object file, that wil have dynamic symbols properly defined. [TODO: Verify]

A similar reasoning was applied to the compilation process. Here, any C declaration in a Go program would require a full-blown C tokenizer and parser and creating such a beast is absolutely not a trivial task. Therefore, authors of cgo have decided to try another route. But this is exactly the part where things get a little wild. The cgo compilation process consists of an interplay between cgo and gcc. cgo generates some specially crafted faulty, but syntactically correct, C files that it passes to gcc. gcc tries to compile the files, but is unable to do so, and therefore returns errors messages. These error messages are then used by cgo to deduce the kinds of the objects defined in the C source code (like if an object is a variable, struct, func, etc.). So cgo uses error messages created by gcc as the official API of gcc. This is pretty wild, no?! 

Nonetheless, after cgo learns enough information about the kinds of objects, it creates another C file containing usage of these kinds of objects. This source file is then (this time successfuly) compiled by gcc and cgo will parse the DWARF section of the resulting object file in order to learn about the type specific information of each object kind (like this struct has these fields, and so on). At the end of this stage, cgo will generate some (pure) Go files containing ... and some C files containing definitions of Go types and runtime callbacks (TODO). gcc will be then called to separetely compile the C sources into object files, and then to link those object files together. This is where the first linking stage comes into play and we can see LDFLAGS being already passed to the gcc at this point. This is the gcc invocation as presented by the go build command (with -x flag):

```console
TERM='dumb' gcc -I . -fPIC -m64 -pthread -fmessage-length=0 -fdebug-prefix-map=$WORK/b001=/tmp/go-build -gno-record-gcc-switches -o $WORK/b001/_cgo_.o $WORK/b001/_cgo_main.o $WORK/b001/_x001.o $WORK/b001/_x002.o $WORK/b001/_x003.o -g -O2 -lcompressor -lz
```

The last '-lcompressor -lz' are exactly flags that I pass to the LDFLAGS in importer.go. '_x001.o.', '_x002.o' and '_x003.o' are object files created from the generated C source code and '_cgo_.o' is an executable that returns after starting, but it contains all the symbols necessary for the C part of the program, i.e. [TODO]. By running readelf -s (or objdump -D), we can see that the compressData symbol is present in that object file. By default (in gcc), the linker uses dynamic libraries (unless -static flag is specified) and therefore these libraries can be seen in the .dynamic section's symbol table of the resulting object file (readelf --dynsym '_cgo_.o'). This is where adding -Wl,-static flag into the LDFLAGS causes problems. The reason is that this flag forces the linker to only look for static libraries, but gcc automatically also passes shared libraries into the ld, including lgcc_s.[^lgcc_s] The linker is thus looking for a libgcc_s.a file, which does not exist (as only libgcc.so exists). We could use -Wl,-Bstatic option with a -Wl,-Bdynamic at the end, but as we have already mentioned, these options are also passed to the external linking stage, which will then have conflicting -static and -dynamic (for some libraries) flags. However, when you pass -static flag into the LDFLAGS, the gcc compiler won't invoke the linker with dynamic libraries included (i.e. with lgcc_s), and the compilation will work (now, even when adding -Wl,-static, as all library names passed to the ld are dynamic).

cgo is then called with --dynimport flag that will parse the _cgo_.o object file and produce go files containing all the definitions that will need to be resolved dynamically. This is needed when a Go itself would reference a C function that will be supplied dynamically (like if our C compression library was a dynamic library). Then it packs all the files together in an archive file, creates files containing references to the Go packages and calls Go linker to create the final binary.  Go then invokes the system linker and the second linking stage starts. Here is the part, where the extldflags are passed together with the LDFLAGS into the system linker to create the final executable. gcc is therefore called with the '-lcompressor -lz' flags from LDFLAGS together with the '-static' flag from the -extldflags command line argument. gcc linker then successfuly prodces a final binary, in our case fully statically linked.

TODO

- support for C++
- support for other compilers
- runtime restrictions of CGO

Footnotes:

[^cgo]: <https://github.com/golang/go/blob/860c9c0b8df6c0a2849fdd274a0a9f142cba3ea5/src/cmd/cgo/doc.go>
[^static]: <https://www.arp242.net/static-go.html> and <https://github.com/golang/go/issues/26492>
[^google_group_issue]: A similar problem was already encountered: <https://groups.google.com/g/golang-nuts/c/7YH5CseB4pc>
[^lgcc_s]: <https://stackoverflow.com/questions/51998912/when-statically-linking-a-library-getting-linker-error-cannot-find-lgcc-s>
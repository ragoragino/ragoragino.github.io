package main

// #cgo CFLAGS: -I${SRCDIR}/compressor
// #cgo LDFLAGS: -lcompressor -lz
// #include <stdlib.h>
// #include "compressor.h"
import "C"
import "unsafe"

func DoCompress(payload []byte) []byte {
	payloadPtr := C.CBytes(payload)
	defer C.free(unsafe.Pointer(payloadPtr))

	resultPtr := C.compressData((*C.char)(payloadPtr))
	defer C.free(unsafe.Pointer(resultPtr))

	if resultPtr.errorCode != 0 {
		panic("Ooh no, zlib compression failed!")
	}

	// TODO: We should check that resultPtr.length can be cast to int
	return C.GoBytes(unsafe.Pointer(resultPtr.output), C.int(resultPtr.length))
}

package main

import "C"

import (
	"bytes"
	"compress/zlib" 
	"io"
	"fmt"
)

func main(){
	inputString := "You talkin' to me? You talkin' to me? Well I don't see anyone else here..." 

	compressedBytes := DoCompress([]byte(inputString))
	
	compressedBuffer := bytes.NewBuffer(compressedBytes)
	decompressedBuffer := bytes.Buffer{}
	r, err := zlib.NewReader(compressedBuffer)
	if err != nil {
		panic("Ohh no, zlib wasn't able to open a NewReader!")
	}
	io.Copy(&decompressedBuffer, r)
	r.Close()

	if inputString != decompressedBuffer.String() {
		panic("Ohh no, compressed and decompressed strings differ!")
	}

	fmt.Printf("Huraay, the compressed and decompressed strings are identical!\n")

	return;
}
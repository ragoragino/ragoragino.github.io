---
layout: post
title:  "Testing client retries on HTTP2 GOAWAY frame"
date:   2022-02-05 13:38:00 +0100
categories: SoftwareEngineering Miscellaneous
---

Recently, when calling GET on certain HTTP2 server I encountered an error of this form:

```
"Transport: cannot retry err [http2: Transport received Server's graceful shutdown GOAWAY] after Request.Body was written; define Request.GetBody to avoid this error"
```

The solution seems pretty obvious (just setting the GetBody callback on http's Request), and after debugging for a while Typhon server library that we are using at work, I found that for clients a body is set on all GET request regardless of the passed body (i.e. even if it is nil). Therefore, after Go's HTTP2 handler received `GOAWAY` from a server, it tried to [retry the request](https://go.googlesource.com/net/+/master/http2/transport.go#544) as `GOAWAY` is [retriable per the specification](https://datatracker.ietf.org/doc/html/rfc7540#page-43): "Receivers of a GOAWAY frame MUST NOT open additional streams on the connection, although a new connection can be established for new streams.". However, because the body was set, but GetBody was not, [retry was aborted](https://go.googlesource.com/net/+/master/http2/transport.go#572). 

Okay, so the solution was simple, let's just set a no-op `GetBody` method, like this:

```
func NewGetRequest(ctx context.Context, path string) typhon.Request {
	httpReq := typhon.NewRequest(ctx, "GET", path, nil)
	httpReq.GetBody = func() (io.ReadCloser, error) {
		return httpReq.Body, nil
	}

	return httpReq
}

```

This should fix the issue. However, I wanted to make sure before deploying the fix that it really does what it is supposed to so. So I embarked on writing a simple test - it consisted of creating an HTTP2 server that would send an HTTP2 `GOAWAY` frame on the first connection, and then would wait for the second connection. The second connection would be handled properly and a response would be sent. 

We start by writing the HTTP2 server with proper TLS configuration:
```
func startHTTP2Server(t *testing.T, certPEM []byte, certPrivKeyPEM []byte, responseBody []byte) (string, func(), error) {
	defaultCloser := func() {}

	serverCert, err := tls.X509KeyPair(certPEM, certPrivKeyPEM)
	if err != nil {
		return "", defaultCloser, err
	}

	tlsCfg := &tls.Config{
		Certificates: []tls.Certificate{serverCert},
		NextProtos:   []string{"h2"},
	}

	l, err := tls.Listen("tcp", "127.0.0.1:0", tlsCfg)
	if err != nil {
		return "", defaultCloser, err
	}

	handlerFinished := make(chan struct{}, 1)

	go func() {
		defer func() {
			handlerFinished <- struct{}{}
		}()

		requestNumber := 0

		for {
			conn, err := l.Accept()
			if err != nil {
				if errors.Is(err, net.ErrClosed) {
					return
				}

				assert.NoError(t, err, "failed listening on a socket")
				return
			}

			log.Printf("New connection accepted.")

			sendGoAway := true
			if requestNumber > 0 {
				sendGoAway = false
			}

			if err := handleConn(conn, responseBody, sendGoAway); err != nil {
				assert.NoError(t, err, "failed handling the connection")
				return
			}

			requestNumber++
		}
	}()

	return l.Addr().String(), func() {
		l.Close()
		<-handlerFinished
	}, nil
}
```

The most important part is the one where we wait for the connections and then handle each connection in a separate function. On the first connection, we pass to the handler a `sendGoAway` boolean set to true. This forces the handler to send a `GOAWAY` frame after receiving the request. We expect a new connection to be created if the `GetBody` is specified on the request. Therefore, we iterate further and wait for a new connection. The second connection is handled as it should be - a response body and a status code of `200` is returned. The connection handler mostly handles different HTTP2 frames, and it looks like this:

```
func handleConn(conn net.Conn, responseBody []byte, sendGoAway bool) error {
	defer conn.Close()

	// This is an HTTP2 preface sent by client
	const preface = "PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
	b := make([]byte, len(preface))
	if _, err := io.ReadFull(conn, b); err != nil {
		return err
	}

	framer := http2.NewFramer(conn, conn)

	for {
		f, err := framer.ReadFrame()
		if err != nil {
			return err
		}

		switch ff := f.(type) {
		case *http2.DataFrame:
			// Send a data frame if the client's stream ended.
			if ff.StreamEnded() {
				if sendGoAway {
					return framer.WriteGoAway(0, http2.ErrCodeNo, nil)
				}

				var hbuf bytes.Buffer
				enc := hpack.NewEncoder(&hbuf)
				writeHeader(enc, ":status", "200")
				writeHeader(enc, "content-type", "application/octet-stream")
				writeHeader(enc, "content-length", strconv.Itoa(len(responseBody)))

				err = framer.WriteHeaders(http2.HeadersFrameParam{
					StreamID:      ff.StreamID,
					BlockFragment: hbuf.Bytes(),
					EndStream:     false,
					EndHeaders:    true,
				})
				if err != nil {
					return err
				}

				err = framer.WriteData(ff.StreamID, true, responseBody)
				if err != nil {
					return err
				}

				return nil
			}
		case *http2.HeadersFrame:
			// ignore as we expect a data frame to be sent afterward
			continue
		case *http2.PriorityFrame:
			// ignore
			continue
		case *http2.RSTStreamFrame:
			return fmt.Errorf("received an unexpected RSTStream frame.")
		case *http2.SettingsFrame:
			var ss []http2.Setting
			ff.ForeachSetting(func(s http2.Setting) error {
				ss = append(ss, s)
				return nil
			})
			if ff.IsAck() {
				err = framer.WriteSettingsAck()
			} else {
				err = framer.WriteSettings(ss...)
			}
		case *http2.PingFrame:
			err = framer.WritePing(ff.Header().Flags&http2.FlagPingAck != 0, ff.Data)
		case *http2.GoAwayFrame:
			return fmt.Errorf("received an unexpected GOAWAY frame.")
		case *http2.WindowUpdateFrame:
			err = framer.WriteWindowUpdate(ff.Header().StreamID, ff.Increment)
		case *http2.ContinuationFrame:
			return fmt.Errorf("received an unexpected CONTINUATION frame.")
		}

		if err != nil {
			return err
		}
	}
}
```

We either ignore or just send some basic responses for most frames, except the data frame. The data frame is the frame where we receive the empty body and a flag that this stream is now half-closed. If the `sendGoAway` flag is set (i.e. on the first connection), we return `GOAWAY` and on returning form the function we also close the connection. If the flag is absent, we just write the headers frame and the data frame containing the payload (which is the second connection).

The test function itself just creates a new `typhon.Request` either with or without a `GetBody` set and it verifies that the client retries on the server's `GOAWAY` frame in the former cases or simply fails in the latter case. The whole program can be found [here](https://github.com/ragoragino/goaway/blob/master/goaway_test.go).
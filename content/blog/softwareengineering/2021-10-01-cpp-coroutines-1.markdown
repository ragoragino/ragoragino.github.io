---
layout: post
title:  "Implementing a TCP server with C++ coroutines. Part I: Introduction & Interface"
date:   2021-10-02 13:35:00 +0100
categories: SoftwareEngineering Miscellaneous
---

C++20 introduced a new language feature called coroutines. This is a well-known tool for programmers in other languages, like Rust (with its async/await pattern), or Go (with goroutines). In C++, one could already use coroutines with the help of Boost library ecosystem. Boost.Asio allowed users to write their code in the event-driven programming style since the beginning of time, but since version 1.54.0 (released in 2013) it had the full support of coroutines thanks to the underlying implementation in Boost.Coroutine library.

Few of these coroutine-implementations (like the one in Boost or in Go) are so-called stackful coroutines. Meaning that when they save the coroutine state they save the whole stack for each individual routine. However, C++20 (and also Rust) use so-called stackless coroutines, which means that they keep their arguments and local parameters on the heap and only push them onto the stack when they are scheduled. The whole control structure is allocated on the heap and it contains only parameters, local variables, and some book-keeping information. From a memory perspective, this is a more terse solution, as the stack requires a non-negligible allocation (from 2Kb for goroutines or around 8Kb for Boost.Coroutine).

I got really excited about this C++ feature, as it allows one to write asynchronous code in an (almost) fully standard (synchronous) manner. However, this language addition is still very low-level, and libraries providing abstractions are only beginning to crop up (e.g. [Lewis Baker's cppcoro](https://github.com/lewissbaker/cppcoro)). As I thought that I did not fully understand the mechanics of how an async library is structured, I decide to begin a quest of writing my own asynchronous mini-library based on these lower-level C++ language features. 

My idea was that in the end, I would love to have a simple TCP server that would be able to asynchronously serve client write and read requests. I got inspired a little bit by the interface of Rust's tokio, so I was thinking usage of the final user-facing library API might look something like this:

```
acairo::Task<void> handle_socket(std::shared_ptr<acairo::TCPStream> stream) {
    try {
        std::vector<char> vector_received_message = co_await stream->read(27);

        const std::string received_message(vector_received_message.begin(), vector_received_message.end());
        
        LOG(l, logger::debug) << "Reading from socket was succesful:" << received_message; 

        const std::string send_message = "Just nod if you can hear me!";
        std::vector<char> vector_message(send_message.begin(), send_message.end());
        co_await stream->write(std::move(vector_message));

        LOG(l, logger::debug) << "Writing to socket was successful."; 
    } catch (const std::exception& e){
        LOG(l, logger::error) << "handle_socket failed: " << e.what(); 
    }
}
```

You might have noticed that I prefixed some objects with the acairo namespace - this will be the name of the library. The actual library calls happen here: `co_await stream->read(27)` and `co_await stream->write(std::move(vector_message))`. The library will communicate (we will see how later) with Linux epoll API to check asynchronously whether the sockets are ready for a particular IO operation (i.e. read or write). If not, then this instance of the coroutine might get set aside and another instance of it might get scheduled.

Except for reading and writing to the TCP sockets, I also wanted to accept new connections asynchronously. In Rust this is achieved inside the tokio's main function by having an attribute `\#[tokio::main]` on the main function. These kinds of attributes are not a feature in C++, so I decided that accepting connections would also be an async function, something like this:

```
acairo::Task<void> handle_accept(std::shared_ptr<acairo::Executor> executor,
    const acairo::TCPListener& listener) {   
    while (true) {
        // Asynchronously accept new connections
        auto stream = co_await listener.accept();

        // Spawn handlers that should handle the connections
        auto handler = std::bind(handle_socket, stream);
        executor->spawn(std::move(handler));
    }
}
```

With `co_await listener.accept()` we wait for the library to signal that we got a new connection. If there is none, the coroutine instance gets put aside, otherwise, it obtains an instance of a shared pointer to the TCPStream which is just a wrapper around the file descriptor of the socket. TCPStream instance is then bound with a handle_socket (shown above) and an executor is called to schedule the handler.

And for the main function, the library would supply a simple `sync_wait` function that would take a function returning an async object (like the `acairo::Task`) as a parameter and wait for its full completion (or an executor being shutdown). So the library could be used like this:

```
int main(){
    // Initialize all the necessary configurations, signals handlers, executor and listener
    
    // Do proper exception handling
    auto f = std::bind(handle_accept, executor, std::ref(listener));
    executor->sync_wait(std::move(f));

    // Properly shutdown executor, listener, and all other dependencies 

    return 0;
}
```

In order to properly implement a library with such an API, I wanted to dig a little deeper into several topics: general concepts related to stackful and stackless coroutines, operating system's file descriptor event interfaces (such as select or epoll on Linux), and coroutine implementation in C++ 20. I will go over each of these areas in detail in order to show how such a library can be implemented.

The article will be divided into three more separate articles. [The first one](https://ragoragino.github.io/softwareengineering/miscellaneous/2021/10/02/cpp-coroutines-2.html) will delve deeper into coroutine theory, [the second one](https://ragoragino.github.io/softwareengineering/miscellaneous/2021/10/02/cpp-coroutines-3.html) will take a closer look at the Linux epoll API and [the last one](https://ragoragino.github.io/softwareengineering/miscellaneous/2021/10/02/cpp-coroutines-4.html) will walk us through the implementation of the async TCP server. The series assume certain knowledge of Linux, networking concepts and some parts assume familiarity with C++.
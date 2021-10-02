---
layout: post
title:  "Implementing a TCP server with C++ coroutines. Part IV: Implementation & Conclusion"
date:   2021-10-02 13:38:00 +0100
categories: SoftwareEngineering Miscellaneous
---

So, we have worked our way through [the coroutine theory](https://ragoragino.github.io/softwareengineering/miscellaneous/2021/10/02/cpp-coroutines-2.html) and [epoll system call family](https://ragoragino.github.io/softwareengineering/miscellaneous/2021/10/02/cpp-coroutines-3.html) and finally, we are ready to apply this knowledge to our task of writing an async server. In this article, we will take a look at the C++20 interface for writing coroutines and show a possible implementation of the TCP server that takes advantage of coroutines and epoll in order to provide relatively high-performant handling of client requests.  

I will be targeting a general overview of the mechanics of C++ coroutines with the use case of an async server, so for the sake of time and space, several of the intricacies of the language feature won't be delved into here. I encourage interested readers to look at the library code itself and continue with reading articles summarised in the references (I would highly recommend going through Lewis Baker's ones as they do a great job of explaining all the potential implementation pitfalls).

A coroutine in C++ is just a function that contains within its body one of the following language-defined expressions: `co_await`, `co_return`, or `co_yield`. `co_await` serves to asynchronously wait for its argument to finish, `co_return` returns a result from a coroutine to the caller, and `co_yield` just yields the execution back to the caller (very similar to the Python's `yield`). We won't be covering the last one in this article because it is used primarily for generators and not async functions.

As was mentioned in the beginning, C++ coroutines are stackless ones, which means they don't preserve a stack frame itself. They keep everything related to the function state in a heap-allocated structure called coroutine frame. This frame contains function arguments, local variables, a promise object, and some bookkeeping info. Arguments and local variables are copied (or moved) to the frame from a stack on the first invocation of coroutine, so that they are preserved across coroutine invocations. On each later resumption of the coroutine, they are pushed onto the stack from the frame. The promise object, which is user-provided, is used as a communication mechanism between coroutine state and the outside world - it controls whether the coroutine suspends at the beginning or at the end (we will see why this can be useful), how it handles exceptions and it participates in returning objects to the caller. Probably the most important part of the bookkeeping portion of the coroutine frame is a resumption point, which is a point where the execution stopped before the last suspension. That allows the execution path to jump to the saved resumption point when a coroutine is resumed. 

This whole machinery of allocating, initializing, and restoring values contained in activation frames is entirely in the hands of the compiler. The language standard defines what kinds of interfaces the user needs to define to satisfy the coroutine properties and the compiler takes care of generating all the boilerplate code. The required interfaces consist of 
1. A return object of the coroutine that will have a `promise_type` defined (i.e. our templated `acairo::Task<T>::promise_type` needs to refer to a well-defined struct/class).
2. This `promise_type` needs to define methods `get_return_object`, `initial_suspend`, `final_suspend`, `unhandled_exception`, and `return_void` (or `return_value` in case the coroutine should return a value).
3. And each `co_await expr` invocation will need to be able to be transformed into an object with `await_ready`, `await_suspend` and `await_resume` methods. This object is referred to as an `Awaiter`, and the `expr` in the `co_await expr` is referred to as an `Awaitable`.

So, the four main structures of the interface are return object type (let's call it `Task`), `Promise` type, and Awaiters and Awaitables. The former two objects live throughout the lifetime of the coroutine - `Task` is mainly used to store a reference to the `std::coroutine_handle` which can be used to resume or destroy coroutines and `Promise` is used as a communication channel between the caller and the coroutine. The latter two classes, `Awaiter` and `Awaitable`, are initialized only when a `co_await` expression is encountered. The `Awaitable` is what we might receive from a lower-level library call (like `stream->read()` in `co_await stream->read()`) or can be some custom object. The `Awaitable` needs to be transformable to the `Awaiter` (via some pre-defined interface methods on `promise_type` or on `Awaitable` itself). The `Awaiter` then decides whether the current coroutine should be suspended or not, and is also called before the coroutine is resumed again. So to recap, `Task` stores `std::coroutine_handle` which manages the execution flow of the coroutine, `Promise` controls communication between the caller and the coroutine, and `Awaiter` is used for customizing coroutine's suspension and resumption behavior.

In general, when a coroutine is encountered, a code similar to the following one is generated by the compiler:

```
{
  promise_type promise;
  auto return_object = promise.get_return_object();
  co_await promise.initial_suspend();
  try { 
    // Our coroutine body is inserted here
  }
  catch (...) { promise.unhandled_exception(); }
final_suspend:
  co_await promise.final_suspend();
}
```

To make clearer the cooperation between the generated code and our own, let's consider a simplified version of the socket handling presented [in the first article](https://ragoragino.github.io/softwareengineering/miscellaneous/2021/10/02/cpp-coroutines-1.html) and sitting [here](https://github.com/ragoragino/acairo/blob/master/src/amain.cpp#L14) in the source code:

```
acairo::Task<void> handle_socket(std::shared_ptr<acairo::TCPStream> stream) {
    std::vector<char> vector_received_message = co_await stream->read(27);

    const std::string received_message(vector_received_message.begin(), vector_received_message.end());

    LOG(l, logger::debug) << "Reading from socket was succesful:" << received_message; 
}
```

The most important portion here is the first assignment, where we asynchronously read from acairo's `TCPStream` object. Implementation of `read` looks like this:

```
acairo::Task<std::vector<char>> TCPStream::read(size_t number_of_bytes) {
        std::vector<char> result(number_of_bytes, 0);

        int remaining_buffer_size = number_of_bytes;
        char* current_buffer_ptr = result.data();

        while(remaining_buffer_size > 0){
            const int number_of_bytes_written = retry_sys_call(::read, m_fd, (void*)current_buffer_ptr, remaining_buffer_size);
            if (number_of_bytes_written < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    co_await ReadFuture(m_executor, m_fd);
                    continue;
                }

                throw std::runtime_error(error_with_errno("Unable to read from the socket"));
            }

            remaining_buffer_size -= number_of_bytes_written;
            current_buffer_ptr += number_of_bytes_written;
        }
        
        co_return result;
    }
```

When reading from a socket, we just loop until we fill the vector with the required number of bytes. Because the socket has a [non-blocking flag set on it](https://github.com/ragoragino/acairo/blob/master/src/tcplistener.cpp#L38), we might get returned an `EAGAIN` or `EWOULDBLOCK` when the socket is not ready for a requested IO operation. In our case, this might mean that there is nothing to read from the kernel socket queue that is shared with the user process. In such a case, we invoke `co_await` operator on `ReadFuture` object. `ReadFuture` is then transformed by the compiler (following some language rules we have adhered to) to an `Awaitable` and later an `Awaiter` object. The `Awaiter` object is in our case `FutureAwaiter`:

```
 template<typename FutureType, 
    typename = typename std::enable_if_t<std::is_base_of<Future, FutureType>::value, FutureType>>
class FutureAwaiter {
    public:
        FutureAwaiter(FutureType&& future_awaitable)
            : m_future_awaitable(std::forward<FutureType>(future_awaitable)) {}
        
        bool await_ready() const noexcept { 
            return false; 
        }

        void await_suspend(std::coroutine_handle<> handle) const noexcept {
            auto continuation_handle = [handle]() mutable {
                handle.resume();
            };

            auto executor = m_future_awaitable.get_executor();

            int fd = m_future_awaitable.get_fd();
            EVENT_TYPE event_type = m_future_awaitable.get_event_type();

            executor->register_event_handler(fd, event_type, std::move(continuation_handle));
        }

        void await_resume() const noexcept {}

    private:
        FutureType m_future_awaitable;
};
```

After an `Awaiter` object is obtained in the `co_await`, the compiler-generated code will call `await_ready` to find whether our coroutine should be suspended or resumed. In our case, we want to suspend it (as we know we have just received `EAGAIN`or `EWOULDBLOCK` indicating the socket is not ready for an IO). When `await_ready` returns `false`, the coroutine is suspended and `Awaiter`'s `await_suspend` method is called. Here we just schedule resuming of the coroutine on the executor (which is our custom library class that primarily handles listening to `epoll` events). As you can notice, we also pass the file descriptor and type of the event to let the executor know, under which conditions it should invoke the callback. The executor will use the `epoll_wait` syscall [mentioned in the previous article](https://ragoragino.github.io/softwareengineering/miscellaneous/2021/10/02/cpp-coroutines-3.html) to wait until the kernel signals that the file descriptor is ready for an IO. Therefore, we will get notified when the particular operation on a socket is ready to be performed. So we got to a point, where our coroutine is suspended and scheduled to be resumed when the socket will become ready. 

In a different thread, we are waiting on `epoll_wait` and monitoring the registered sockets. The registration process happened just after a new connection was established. We called `Executor`'s `register_fd` method from the constructor of a `TCPStream` which then invoked the `epoll_ctl` syscall like this:

```
void Executor::register_fd(int fd) const {
    LOG(m_l, logger::debug) << "Registering fd [" << fd << "] to the epoll interest list.";

    // We need edge-triggered notifications as we will be invoking handlers based on incoming events.
    // We start listening on EPOLLIN and EPOLLOUT at the same time, although the user might only need one
    // direction. However, by using edge-triggered notifications, we shouldn't worsen our perf.
    struct epoll_event accept_event;
    accept_event.events = EPOLLIN | EPOLLOUT | EPOLLET;
    accept_event.data.fd = fd;
    if (retry_sys_call(epoll_ctl, m_epoll_fd, EPOLL_CTL_ADD, fd, &accept_event) < 0) {
        throw std::runtime_error(error_with_errno("Unable to add new socket to the epoll interest list"));
    }
}
```

The `epoll_wait` syscall is happening in a separate thread where we just loop until `Executor` is sent a shutdown command:

```
void Executor::run_epoll_listener() {
        auto events = std::make_unique<struct epoll_event[]>(m_config.max_number_of_fds);

        auto schedule_ready_tasks = [this](const SocketEventKey& event_key){
            // Try emplacing an event key if one is not in the map. That way we can call 
            // a handler that will be registered later.
            auto [it, ok] = m_coroutines_map.try_emplace(event_key, m_scheduler);
            it->second.schedule();  
        };

        while (!m_stopping) {
            const int count_of_ready_fds = retry_sys_call(epoll_wait, m_epoll_fd, events.get(), m_config.max_number_of_fds, 10); 
            if (count_of_ready_fds < 0) {
                throw std::runtime_error(error_with_errno("Waititing for epoll_events failed"));
            }

            std::unique_lock<std::shared_mutex> lock(m_coroutines_map_mutex);
            for (int i = 0; i < count_of_ready_fds; i++) {
                const struct epoll_event& event = events.get()[i];
                const int fd = event.data.fd;

                // In case of a socket error, let the handlers finish their work
                if (event.events & EPOLLERR) {
                    log_socket_error(fd);

                    SocketEventKey event_key_in{fd, EVENT_TYPE::IN};
                    schedule_ready_tasks(event_key_in);

                    SocketEventKey event_key_out{fd, EVENT_TYPE::OUT};
                    schedule_ready_tasks(event_key_out);

                    continue;
                }
            
                // EPOLLIN and EPOLLOUT should be also called when the peer closed that end of the socket.
                // Therefore, it doesn't seem that we need to handle any special events connected
                // with unexpected peer socket shutdown here.
                if (event.events & EPOLLIN) {
                    LOG(m_l, logger::debug) << "Adding handler for a fd " << fd << " and event_type " 
                        << EVENT_TYPE::IN << " to the scheduler's queue.";

                    SocketEventKey event_key{fd, EVENT_TYPE::IN};
                    schedule_ready_tasks(event_key);
                }

                if (event.events & EPOLLOUT) {
                    LOG(m_l, logger::debug) << "Adding handler for a fd " << fd << " and event_type " 
                        << EVENT_TYPE::OUT << " to the scheduler's queue.";

                    SocketEventKey event_key{fd, EVENT_TYPE::OUT};
                    schedule_ready_tasks(event_key);
                }
            }
        }
    }
}
```

In the while loop, we just wait until the `epoll_wait` returns a positive number of file descriptors that should be ready for an IO operation. Afterward, we check what events happened on those sockets and schedule coroutine resumptions that were registered before from the `FutureAwaiter`'s `await_suspend`. After the scheduler (which is our custom multi-threaded simple FIFO scheduler) starts executing the callbacks, firstly, `await_resume` is called (but we do not need any customization for resuming, so it is a noop for us) and then the execution jumps to the resumption point. In our case, we just repeat reading from the socket until we fill the buffer with a specified number of bytes.

One thing I haven't mentioned is what happens to the caller coroutines when a called coroutine is suspended - in our case, what happens to the `handle_socket` when `TCPStream::read` is suspended? So, immediately after `stream->read(27)` is suspended, it will return the `Task` instance that was obtained by calling `get_return_object` at the beginning of the coroutine. However, because we are `co_await`-ing reading from the socket (i.e. `co_await stream->read(27)`), we will need to obtain an `Awaiter` from the `Task`. We have defined a special method to do the transformation and the result is an instance of the `TaskAwaiter` type:

```
template<typename T>
class TaskAwaiter {
    public:
        TaskAwaiter(std::coroutine_handle<Promise<T>> handle)
            : m_handle(handle) {}
        
        bool await_ready() const noexcept { 
            return m_handle.done();
        }

        // We return a coroutine_handle here instead of just bool. The reason is that we always 
        // suspend when promise.initial_suspend is called, and here we just resume the suspended
        // coroutine shortly afterward. For reasons this is useful, have a look at:
        // https://lewissbaker.github.io/2020/05/11/understanding_symmetric_transfer
        template<typename ContinuationPromiseType>
        std::coroutine_handle<> await_suspend(std::coroutine_handle<ContinuationPromiseType> handle) const noexcept {
            m_handle.promise().set_continuation(handle);
            return m_handle;
        }

        T await_resume() const {
            return m_handle.promise().get_return_value();
        }

    private:
        std::coroutine_handle<Promise<T>> m_handle;
};
```

As you can see, we initialize `TaskAwaiter` with the handle to the coroutine saved in `Task`, i.e. the handle of the called `TCPStream::read` coroutine (let's call it a reading coroutine). After the `TaskAwaiter` is obtained, its `await_`-prefixed methods will be called to decide whether to suspend the current coroutine or not. As we know that the `handle_socket` coroutine (let's call it a socket handling coroutine) cannot continue without the read finishing, so `await_ready` just checks whether the coroutine finished. If it has finished, we can merrily continue with the execution, as `await_resume` will be able to get a return object from a promise. Otherwise, `await_suspend` is called - and what we do here, is we chain the current coroutine (i.e. the socket handling one) that is passed as an argument to the function into the reading coroutine. That way, when a reading coroutine finishes, it will be possible to resume this one. This should be done when `co_await promise.final_suspend()` is called at the end of the reading coroutine, as at that point the reading coroutine is suspended for the last time and cannot be resumed. Our promise returns a `ContinuationAwaiter` from `final_suspend` that looks similar to this:

```
template<typename T>
class ContinuationAwaiter {
    public:
        ContinuationAwaiter() noexcept {};
        
        bool await_ready() const noexcept { 
            return false; 
        }

        template<typename PromiseType>
        std::coroutine_handle<> await_suspend(std::coroutine_handle<PromiseType> handle) const noexcept {
            return handle.promise().get_continuation();
        }

        void await_resume() const noexcept {}
};
```

We always suspend this coroutine, as we want to resume potential continuations. This is done in `await_suspend`, where we return the continuation. In our case, it will be always a valid continuation and the compiler-generated code will resume it. This is the place, where `handle_socket` gets called once again. As the first thing that will be executed before the coroutine itself will continue is the call to `TaskAwaiter`'s `await_resume` (remember, we are awaiting on the TaskAwaiter here: `co_await stream->read(27)`) that will return the return object of the finished coroutine (i.e. our `TCPSream::read` coroutine). This will assign the `vector_received_message` in `std::vector<char> vector_received_message = co_await stream->read(27)` and we are ready to continue with the socket handling. Because returning a coroutine handle from `await_suspend` is a tail-call, there are no limits to how deep coroutine call graphs can become!

I will also briefly mention how coroutines get destructed. When a coroutine is destructed, the promise object is destructed first, then destructors of function parameters are called, and finally, the coroutine frame itself is deallocated. Local variables are destroyed just before the call to `promise.final_suspend()` as the coroutine cannot be resumed afterward, so we shouldn't need to access local variables after that point. A coroutine can be destructed in multiple ways - either by a call to `handle.destroy` which explicitly starts this process of coroutine frame destruction. In addition, any coroutine that falls off its body (in our case this would mean finishing `await_resume` after `true` was returned from `ContinuationAwaiter`'s `await_ready`) is automatically destroyed. A last possible case is when an uncaught exception is thrown from the coroutine. Exceptions in coroutines could be a topic for a separate article, and I invite you to either look at the code or read some of the references to get a deeper understanding of this area.

### Conclusion

Perfect, so we have sketched the lifecycle of a coroutine and all the objects that participate in suspending and resuming it. We have seen how it all comes together and how it allows us to implement a simple asynchronous TCP server. I can imagine the last piece of this series might have been quite dense, so I encourage you to either go through [the source code](https://github.com/ragoragino/acairo) or take a look at the sources below.

**Sources** \
[https://blog.panicsoftware.com/coroutines-introduction/](https://blog.panicsoftware.com/coroutines-introduction/) \
[https://lewissbaker.github.io/2017/09/25/coroutine-theory/](https://lewissbaker.github.io/2017/09/25/coroutine-theory/) \
[http://www.vishalchovatiya.com/cpp20-coroutine-under-the-hood/](http://www.vishalchovatiya.com/cpp20-coroutine-under-the-hood/) \
[https://luncliff.github.io/coroutine/ppt/[Eng]ExploringTheCppCoroutine.pdf](https://luncliff.github.io/coroutine/ppt/[Eng]ExploringTheCppCoroutine.pdf)
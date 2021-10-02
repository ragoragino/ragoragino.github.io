---
layout: post
title:  "Implementing a TCP server with C++ coroutines. Part III: epoll"
date:   2021-10-01 13:35:00 +0100
categories: SoftwareEngineering Miscellaneous
---

#### epoll

In the last article, we have seen what are coroutines and what are the differences between stackless and stackful coroutines. Today, we will move on and will take a look at how one can take advantage of the facilities provided by Linux to achieve great performance for IO-bound programs.   

Userspace processes usually take advantage of the OS's networking API as writing a separate networking implementation is an absolutely non-trivial task. As a result, they need kernel support to receive information whether file descriptors are ready for IO operations. On Unix systems, `select` and `poll` were traditional Unix syscalls that userspace processes querried for update on file descriptor's IO state. However, they were largely superseded by newer syscall families, like `epoll` on Linux or `kqueue` on BSD, which constitute the current standard.

Because `select` and `poll` share large parts of their implementations, we will just focus on the latter one. `poll` is invoked with an array of structures (of type `pollfd`) containing file descriptors. We are telling the kernel that we want to check whether any of these file descriptors is ready for an IO operation. Kernel copies the array and iterates over it to verify which file descriptors are ready. The kernel then needs to iterate over the file descriptors and sets a flag on a field of `pollfd` for each element of the original array that is ready. Userspace programs then need to iterate over the passed `pollfd` array to find out which file descriptors have the flag set. As one can see, there are several performance problems with this approach - the first one is related to copying the input array containing all file descriptors that need to be monitored on every single syscall, and the other one is that kernel and the application both need to iterate over the entire set to resolve ready file descripts. These operations are O(n) in the number of file descriptors and are almost prohibitive for high-performance servers.

`epoll` family of syscalls (we will focus on `epoll` as it is a Linux syscall) were designed to overcome the difficulties with scaling IO readiness monitoring to a large number of file descriptors. The way how it does it is by separating registering a file descriptor from reading its IO status. The kernel thus keeps a persistent list of monitored file descriptors instead of relying on getting it passed fully in each call.

The syscall `epoll_ctl` is used primarily to either add or remove file descriptor from the monitored set, or update the events which kernel should monitor on that file descriptor. After registering file descriptor in the kernel, `epoll_wait` can be to used to wait for the events userspace program is interested in. `epoll_wait` returns the number of file descriptors that are ready and it updates a passed-in array of `epoll_event` types with the events that happened on those file descriptor. It is then the callers job to iterate over the returned array and invoke some custom action. Kernel authors decided to allow callers to choose between so-called level and edge triggered notifcations. The former ones mean that the kernel will keep returning the occurence of the monitored IO as long as the IO readiness status stays the same. For example, in case of socket that has finished buffering some peer data, `epoll_wait` will keep returning that file descriptor until we read the data from the socket. In case of edge-triggered notifications, `epoll_wait` will notify us only once the status changed. The type of notifications can be set per-file descriptor by passing a particular parameters to the `epoll_wait` (default is level-triggered, for edge-triggered one needs to pass `EPOLLET`).

Except `epoll_wait` and `epoll_ctl`, the last syscall of the family is `epoll_create` and it is used to create a new file descriptor that will be used in the previous two syscalls to identify the epoll instance. An example of the whole chain of epoll usage might look like this:

```
// Let's imagine we have fd variable set to some file descriptor we want to asynchronously monitor for IO

// Create the epoll file descriptor
int epoll_fd = epoll_create1(0);
if (epoll_fd < 0) {
    std::stringstream ss{};
    ss << "Unable to create a fd for epoll: " << strerror(errno) << ".";
    throw std::runtime_error(ss.str());
}

// Add the file descriptor to the epoll interest list
struct epoll_event accept_event;
accept_event.events = EPOLLIN | EPOLLOUT | EPOLLET;
accept_event.data.fd = fd;
 if (epoll_ctl(epoll_fd, EPOLL_CTL_ADD, fd, &accept_event) < 0) {
    std::stringstream ss{};
    ss << "Unable to add new file descriptor to the epoll interest list: " << strerror(errno) << ".";
    throw std::runtime_error(ss.str());
}

// In a loop wait for any event on the file descriptor to occur
const int max_number_of_fds = 1;
const int wait_timeout = 10;
auto events = std::make_unique<struct epoll_event[]>(max_number_of_fds);
while (true) {
    const int count_of_ready_fds = epoll_wait(epoll_fd, events.get(), max_number_of_fds, wait_timeout); 
    if (count_of_ready_fds < 0) {
        std::stringstream ss{};
        ss << "Waiting for epoll_events failed: " << strerror(errno) << ".";
        throw std::runtime_error(ss.str());
    }

    for (int i = 0; i < count_of_ready_fds; i++) {
        if (event.events & EPOLLERR) {
            // Do something with the failure
            continue;
        }
    
        if (event.events & EPOLLIN) {
            // Do something with the input ready event
        }

        if (event.events & EPOLLOUT) {
            // Do something with the output ready event
        }
    }
}
```

We start by creating the epoll file descriptor instance (`epoll_create`), and then we register the file descriptor to the epoll interest list (`epoll_ctl`). Afterward, we wait for an IO event to become ready by calling `epoll_wait`. Its return value will inform us whether there is any IO event ready to be performed on one of the monitored file descriptors (we are monitoring only one, so `count_of_ready_fds` will be <= 1). Finally, we should handle the flagged event and continue in waiting for the new ones.

Although epoll seems like a really helpful kernel sycall family, it has its own issues. The first one concerns getting multi-threaded epoll waiting right. If one would like to call `epoll_wait` from multiple threads, it is very easy to get into race-conditions without some additional flags passed when registering file descriptors. The other issue is that epoll implementation actually doesn't view registered objects as file descriptors, but as their kernel counterparts (so-called file descriptons). This can cause subtle bugs where a file descriptor might be closed, but the epoll instance is still listening as the underlying kernel object is alive. The solution is to always deregister a file descriptor by calling `epoll_ctl(epoll_fd, EPOLL_CTL_DEL, fd)` before closing it. A wonderful exposition of these issues can be found in these articles of Marek Majkowski, [part 1](https://idea.popcount.org/2017-02-20-epoll-is-fundamentally-broken-12/) and [part 2](https://idea.popcount.org/2017-03-20-epoll-is-fundamentally-broken-22/).

##### Sources
https://unixism.net/2019/04/linux-applications-performance-introduction/
https://jvns.ca/blog/2017/06/03/async-io-on-linux--select--poll--and-epoll/
https://idea.popcount.org/2017-02-20-epoll-is-fundamentally-broken-12/
https://idea.popcount.org/2017-03-20-epoll-is-fundamentally-broken-22/
---
layout: post
title:  "Implementing a TCP server with C++ coroutines: Part II: Coroutine Theory"
date:   2021-10-02 13:36:00 +0100
categories: SoftwareEngineering Miscellaneous
---

To start our journey of writing an async TCP server, we might want to understand how asynchronous code might work with coroutines. Asynchronous code might be implemented in multiple ways. The first approach is to rely on the OS. This would entail calling blocking OS system calls (like `read` or `write`) and letting the OS scheduler park waiting threads. However, context-switching between threads is an expensive operation, as it involves saving a couple of CPU registers and flags, and also invalidating entries in the caches. A second possible solution would be to write the IO-intensive part of the program as a series of chained callbacks where each callback will be called after an IO operation occurs. Even though this avoids the performance overhead of thread context-switching, the resulting programs can be unusually difficult to read. A solution to both of these problems might be coroutines. 

Coroutines are a generalization of functions (also called routines). For a classical function, an execution flow starts by preparing for a call, then the call itself, and after the call is finished, we return back to the caller. On 64bit x86 instruction set, the first part will consist of pushing function parameters and the address of the next instruction onto the stack. After jumping to the starting address of the called function, all the local variables that will get allocated afterward will be pushed onto the stack frame of the current function. When a called function returns, the return object (if any) is saved into a specified register, local variables are destructed and the `ret` instruction then jumps to the return address that was pushed onto the stack before by the caller.

A coroutine generalizes this behavior as instead of flowing linearly from the start until the return, it may get suspended at an arbitrary point. Therefore, instead of just call and return operations, we can also control its suspend and resume behavior. When a program is IO-bound, with normal functions, one would have to resort to either leaving the scheduling of different operations on the OS (via context-switching) or writing the code as a series of callbacks to be called when a specified IO-operation finishes. Coroutines solve both problems - the scheduling doesn't involve the OS now as it is completely cooperative and can be therefore much more performant, and the written code is more readable as it resembles normal synchronous code.

There are two main types of coroutines - stackful and stackless. The former ones maintain a separate stack for its execution, which requires potentially larger memory size (although some implementations provide flexible stack resizing). But because of this, they allow for deeply nested suspensions, where a coroutine can get suspended in any of the routines that it calls. Stackless coroutines reuse the stack of the caller which avoids memory allocating stack. However, because they don't own their own stack, they can get suspended only in their own code. So stackful coroutines are more powerful, but at the cost of lower efficiency, while stackless ones are a little less generic, but are more performant. This is probably the reason why C++ language authors decided to go with the stackless ones as they provide "zero-overhead abstraction", which is one of the design principles of the language itself.

The stackful coroutines are generally implemented by allocating data structure (called traditionally context) containing separate stack, CPU register content, and CPU flags. Then, when a switch to another coroutine is invoked by some explicit call, the CPU registers and flags are saved in the context of the suspending coroutine and are popped onto the CPU from the context of the resuming coroutine. E.g. an example of a stackful coroutine (also called fiber) from Boost.Context library might look like this:

```
namespace ctx = boost::context;

int a;

ctx::fiber source{[&a](ctx::fiber&& sink){
    a = 0;
    int b = 1;
    for(;;) {
        sink = std::move(sink).resume();
        int next = a + b;
        a = b;
        b = next;
    }

    return std::move(sink);
}};

for (int j = 0; j < 10; ++j) {
    source = std::move(source).resume();
    std::cout << a << " ";
}
```

Re-assignments `sink = std::move(sink).resume()` and `source = std::move(source).resume()` are the places where fibers switch between each other. Don't ponder too much on the specifics of the `std::move(sink).resume()` expression, that's just C++ being C++ (and also a little bit of Boost being Boost). 

A goroutine implementation in Go relies on a similar mechanism, where each goroutine is represented internally by a stack and a CPU state (among other things). During a goroutine context switch (invoked e.g. by an IO syscall that would block), running goroutine parks itself and a new one is scheduled instead. 

Because stackul coroutine libraries rely on the manipulation of CPU state, they need to have implementations for all the instruction set architectures they want to support. However, they don't require any special compiler support (except being able to manipulate CPU registers). Also, if we were to call another function from inside the `source` fiber, it is possible to resume the `main` fiber from that function also. This is exactly what we meant by stackful coroutines allowing for deeply nested suspensions.

On the other hand, stackless coroutines require compiler support and can only suspend within their own body as they are tightly coupled with their callers. There are multiple potential implementations of stackless coroutines. This is something I couldn't find any great resources on, but by reading Clang's docs I feel that they resorted to separating coroutine function into a ramp function and the remainder. The ramp function executes until a first suspension point is reached. For the rest of the coroutine, for each block of code between possible suspensions a separate function is generated. These functions then handle where the next resumption point is. I am not completely sure this will be the correct description, but I imagine that for a coroutine like this:

```
Task coroutine_func(int a) {
    int b = 1;
    co_await SomeFuture1(a, b);
    int c = 2;
    co_await SomeFuture2(b, c);
}
```

the compiler might generate a coroutine frame containing following fields and methods:

```
struct __coroutine_func_frame {
    __coroutine_func_frame(int a_) : a{a_}{}

    void ramp() {
        b = 1;

        // Generated code related to co_awaiting SomeFuture1
        // ...

        current_suspension_point = 1; 
    }
    
    void __suspension_point_2() { 
        int c = 2;

        // Generated code related to co_awaiting SomeFuture2
        // ...

        current_suspension_point = 2;
    } 

    void __suspension_point_3() { 
        // Do some final operations
    } 

    int a, b; // These variables need to be accessible across suspension points

    void (*suspension_callbacks)[] = {__suspension_point_1, __suspension_point_2, __suspension_point_3};
    int current_suspension_point{0};
};
```

Resuming the coroutine is then just calling the suspension callback with a current suspension point index. As one can see, this approach doesn't require any CPU state manipulation and is therefore ISA-independent. However, compiler support is required. 

We now know the basic theory of coroutines and what are the differences between stackful and stackless ones in terms of their performance and memory characteristics and implementations. In the next article, we will continue looking at how our possible TCP server implementation might take advantage of OS's epoll APIs to provide responsive and scalable handling of clients' requests. 

**Sources** \
https://blog.panicsoftware.com/coroutines-introduction/ \
https://dmitrykandalov.com/coroutines-as-threads \
https://lewissbaker.github.io/2017/09/25/coroutine-theory \
http://www.vishalchovatiya.com/cpp20-coroutine-under-the-hood/ \
https://gcc.gnu.org/legacy-ml/gcc-cvs/2020-01/msg01576.html \
https://llvm.org/docs/Coroutines.html \
https://luncliff.github.io/posts/Exploring-MSVC-Coroutine.html \
https://www.italiancpp.org/2016/11/02/coroutines-internals/ \
https://golang.org/src/runtime/proc.go
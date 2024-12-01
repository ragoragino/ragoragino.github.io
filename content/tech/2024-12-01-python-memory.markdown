---
layout: post
title:  "Analysing Memory of Python Programs"
date:   2024-12-01 10:00:00 +0100
categories: SoftwareEngineering Miscellaneous
---

I recently conducted a performance analysis of several critical production services to optimize their overall performance. The primary goals were to optimize CPU time and eliminate existing memory leaks to better utilize our hardware resources. Having done extensive Go performance analysis in the past, I found that thanks to the language's excellent tooling (primarily the [pprof](https://pkg.go.dev/net/http/pprof) package), it's relatively straightforward - almost like a walk in the park. Both CPU and memory analysis simply require hitting the HTTP endpoints and analyzing the resulting data. For Python profiling, I had experience with [py-spy](https://github.com/benfred/py-spy) a terrific sampling profiler. One of py-spy's major advantages over other Python tools is its ability to profile running Python programs, which is crucial when analyzing performance issues in production or production-like environments

However, I had never conducted a deep analysis of memory usage and potential leaks in Python programs (specifically those using the CPython interpreter, which I'll focus on in this article). I was genuinely surprised by how challenging it was to obtain meaningful results. The main constraint was the need to analyze memory usage in a production-like environment, as the CPU and memory issues only manifested during significant live traffic processing. While I could have isolated and tested individual components, replicating the full production setup and testing all possible paths would have been time-consuming. Therefore, I decided to investigate tools that would enable memory analysis of a running Python process.

### Structure of Python memory

Python abstracts memory management away from programmers (unlike C or C++), with memory allocation handled automatically by the Python interpreter. When creating any new object—whether a string, dictionary, or class instance—the Python interpreter initializes it on the heap. Along with a pointer to the data, each object stores a [reference count](https://docs.python.org/3/c-api/refcounting.html). This count enables the interpreter to automatically deallocate memory when the reference count reaches zero (similar to how std::shared_ptr<T> works in C++). This approach cleverly avoids expensive [garbage collection](https://docs.python.org/3/library/gc.html) by immediately identifying unused objects. While Python does provide a garbage collection mechanism for handling reference cycles, reference counting remains the primary form of memory management.

Python initializes all objects on the heap alongside their reference counts, but this raises a question: How is this memory organized? The answer lies in Python's structured memory layout system of [arenas, pools, and blocks](https://docs.python.org/3/c-api/memory.html).

### Arenas / Pools / Blocks

For most objects (specifically, those smaller than or equal to 512 bytes), Python employs a system of pre-allocated memory chunks to initialize objects. These chunks are organized hierarchically as arenas, pools, and blocks in descending order of size. Arenas, the largest units at approximately 1MB on 64-bit platforms, are obtained from the system allocator. The Python memory allocator (pymalloc) then subdivides these arenas into pools, typically matching the system page size of 4KB. Each pool is dedicated to objects of a specific size range, with objects allocated as blocks within their respective pools. When initializing a new object, Python's memory allocator first determines the object's size class, then allocates it within either an existing or new pool associated with that size range. Upon deallocation, the object's space is added to a free list for future allocations

Pymalloc only interacts with the system (affecting RSS/PSS metrics) during arena allocation or deallocation, with deallocation occurring only when all pools within an arena are completely empty. This behavior can create challenges for long-running processes, particularly when services handle both temporary data (such as requests and Kafka messages) and long-lived data (like cache entries). The resulting memory fragmentation often manifests as numerous nearly-empty pools containing just a few long-lived objects. To mitigate memory growth caused by this fragmentation, it's recommended to allocate all long-lived objects during service initialization

As previously discussed, pymalloc handles allocations only for objects less than or equal to 512 bytes. For larger objects (such as large strings), pymalloc bypasses its internal system and directly uses the system allocator. Similarly, when executing C extensions, memory allocation occurs directly in the native C/C++ code, bypassing the Python allocator entirely.

### Memory analysis

Given the complexity and diversity of Python's memory allocation system, analyzing memory-related issues requires a systematic approach. The first step is to confirm and measure memory growth in your process. This can be accomplished through either environment-specific monitoring tools (such as AWS CloudWatch metrics for cloud-based applications) or process metrics (primarily RSS process size, or preferably PSS, which provides proportional memory measurement when dealing with shared libraries).

After confirming the presence of a memory leak, the next step is identifying its source. While memory leaks in shared libraries warrant their own discussion, this analysis will focus on leaks within the main application.

Two main approaches exist for identifying memory leaks in Python applications: analyzing heap snapshots across different time points, or examining allocations at successive intervals. I employed guppy for heap snapshot analysis and tracemalloc for allocation tracking. While both tools provide similar insights, each proves more valuable in specific scenarios.

#### Guppy

[Guppy](https://smira.ru/wp-content/uploads/2011/08/heapy.html) is a mature Python package that provides comprehensive Python heap snapshots. Its power lies in its ability to analyze heap structure, allowing developers to traverse entire object reference trees—for instance, tracing the complete chain from allocated strings to their containing class. This capability, however, comes at a cost: to generate its detailed heap map, Guppy must iterate through all live objects and create mappings between them, making it both slow and memory-intensive in complex environments. The tool has a steep learning curve, particularly in understanding the various object [relationships](https://zhuyifei1999.github.io/guppy3/heapy_Use.html#heapykinds.Use) and applying them effectively in analysis. Yet once mastered, Guppy enables detailed analysis through successive heap snapshots, revealing the evolution of suspicious objects and their relationships (including both referring object types and specific referring fields). For my production environment analysis, I implemented a temporary endpoint to execute Guppy queries against the running service

```
class MemoryTracer:
  def eval_expr_endpoint(self):
    """
    This endpoint is used to evaluate any expressions, mostly useful for tracking memory usage.

    Usage:
    curl -v -X POST -d '{"expr": "logging.info(self._format_heap(h[1].byvia))"}' "Content-Type: application/json"  http://localhost:80/debug/memory_summary
    """

    request_json = json.loads(request.body.read())
    expr = request_json["expr"]

    hp = hpy()
    h = hp.heap()

    exec(expr)

    del h
    del hp

    return
```

Beyond its complexity, Guppy has another significant limitation: it cannot automatically trace memory not managed by pymalloc. While C extension developers could theoretically implement special interfaces to enable this functionality, this rarely occurs in practice. Consequently, memory leaks within C extension packages are typically undetectable using Guppy.

#### Tracemalloc

For tracking memory allocation evolution, I utilized [tracemalloc](https://docs.python.org/3/library/tracemalloc.html), an official Python library. Tracemalloc generates a sorted list of source code locations requesting pymalloc memory allocations, ranked by frequency and size. However, interpreting these numbers requires careful consideration for long-running services, as they typically allocate substantial temporary memory (for request buffers, concurrency handling, and data encoding/decoding) that may not indicate memory leaks. The key is to identify allocations whose aggregate size increases over time. Notably, tracemalloc operates with significantly lower memory and CPU overhead compared to Guppy. Like with Guppy, I implemented an API endpoint to retrieve tracemalloc data from a running service:

```
class MemoryTracer:
  def __init__(self):
    tracemalloc.start()
    self._old_snapshot = tracemalloc.take_snapshot()

  def _update_tracemalloc_snapshot_endpoint(self):
    """
    This endpoint is used to update and print the tracemalloc snapshot.

    Usage:
    curl -v "Content-Type: application/json"  http://localhost:80/debug/memory_summary
    """

    snapshot_new = tracemalloc.take_snapshot()
    top_stats = snapshot_new.compare_to(self._old_snapshot, "lineno")
    for stat in top_stats[:25]:
      logging.info(stat)
      for line in stat.traceback.format():
        logging.info("------------ {line}")

    self._old_snapshot = snapshot_new
```

### Results

These tools can effectively identify memory leaks in production Python services. In constrained environments (such as unit tests or simple scripts), implementation is straightforward, and the debugging iteration cycle is quick. However, analyzing running services under non-trivial traffic presents significant challenges, including workers crashing due to OOM conditions or failing health checks (due to CPU hijacking by the tool). Once properly configured for these complex environments, the tools enable local analysis of collected memory data.

While both tools provide similar allocation statistics and can effectively detect memory leaks, tracemalloc offers a more user-friendly approach by directly identifying high-memory-consumption code locations. Though more complex to use, Guppy serves as a valuable complementary tool, particularly when tracemalloc's output becomes noisy due to high volumes of temporary allocations in production traffic.

For investigating memory leaks outside the primary pymalloc allocation path, developers can employ Valgrind, a tool widely used in C/C++ development. Valgrind can detect memory leaks in C extensions, though this topic warrants its own discussion and can be explored further in the referenced sources below.

### Sources

In addition to the official Python and library documentations, some good articles on the topic are:

- https://blog.krudewig-online.de/2022/09/04/locating-memory-leaks-in-services-part1.html
- https://blog.krudewig-online.de/2022/09/12/locating-memory-leaks-in-services-part2.html
- https://rushter.com/blog/python-memory-managment/
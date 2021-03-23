---
layout: post
title:  "eBPF explorations"
date:   2021-03-06 11:44:00 +0100
categories: SoftwareEngineering Miscellaneous
---
During the last few days, I have been digging into the extended Barkeley Packet Filters (eBPFs). I found out about this technology when I was learning about the internal machinery of tcpdump.[^tcpdump] tcpdump works by attaching a filter during the creation of the socket, which causes all network packets to be also routed to this filter. Kernel requires these filters to be passed in the form of a BPF bytecode, which is a specific bytecode that can execute only a restricted set of operations. The reason kernel allows only a pre-specified instruction set is to remove potential security vectors associated with running userspace programs inside the kernel. The bytecode is after verification either executed by the BPF interpreter running in the kernel or JIT-compiled to the native code. The filter is always called when a new packet arrives and passes interesting packets to the userspace via a file descriptor. 

Kernel developers spotted a huge potential that this type of kernelspace - userspace communication could provide to the larger community and hence extended BPF was born. eBPF is not only a packet filter but provides very generic hardware, kernel, and userspace tracing and monitoring functionalities. One can attach a BPF program to almost all kernel functions (that are non-inlined), user functions, hardware counters, and a lot more. It is a pretty powerful beast and there has been a lot of traction behind it lately. A major architecture difference between classical and extended BPF is that the extended version's bytecode is 64bit and has a larger instruction set. Additional improvements in the interaction with the kernel include a decent amount of data structures that are shared between kernel and userspace.

Even though it might sound like eBPF is the best, there are still some limits to its power. Debugging of BPF programs can be pretty cumbersome, the majority of kernel calls are implementations and therefore not API-stable, and as I have already mentioned, the set of allowed BPF instructions is highly limited and therefore one cannot use all the constructs available in higher-level programming languages. 

I wanted to get my hands dirty by trying to write a simple eBPF program tracing some kernel function. There is a couple of options on how to go about doing it. One is to write a BPF program, compile it into a BPF bytecode and directly pass this object file to the bpf syscall (or command-line tools providing a similar interface, like tc). There is also a project called bpftrace where bpf programs are written in a specific awk-inspired language and executed by bpftrace command line tool.[^bpftrace] However, I have chosen a third option which is to use a very convenient wrapper around creating BPF programs in C called bcc that allows users to develop new eBPF programs without the hassle of compiling their sources into BPF bytecode. It has pretty neat bindings from Python and Lua. So I just installed and compiled bcc library.[^bcc_install] Before installing, one just needs to check that the kernel version was compiled with all the necessary flags for running eBPF programs.[^bcc_kernel_config]

When talking about tracing kernel calls, there are two types of such traces, kprobes and kretprobes. The first one is invoked before the execution of the traced function, the latter one is called when the traced function returns. These hooks are invoked thanks to the replacement of instructions in the kernel's code at runtime (for details about how it works, see: [^kprobe]).

On the bcc project's Github page, they host a couple of examples and tools one can use as an inspiration. I just wanted to create a simple showcase, so I thought I could trace ping calls on my machine. I would initiate a ping request from another machine on my LAN (or using Python's scapy package on the same machine), and trace those requests on the target machine running an eBPF program. The eBPF program will need to have access to the protocol field in the IP header because ping requests are actually Echo Request messages of the ICMP protocol. ICMP protocol is sent inside the IP packets with the protocol field in the IP header set to 1 (TCP and UDP are 6 a 17 respectively). Therefore, we might consider tracing ip_rcv kernel function that handles the entry part of the IP layer processing in the kernel. So let's see how does ip_rcv look like (in my case for a 5.4 kernel version):

```c
/*
 * IP receive entry point
 */
int ip_rcv(struct sk_buff *skb, struct net_device *dev, struct packet_type *pt,
	   struct net_device *orig_dev)
{
	struct net *net = dev_net(dev);

	skb = ip_rcv_core(skb, net);
	if (skb == NULL)
		return NET_RX_DROP;

	return NF_HOOK(NFPROTO_IPV4, NF_INET_PRE_ROUTING,
		       net, NULL, skb, dev, NULL,
		       ip_rcv_finish);
}
```

As we can see, there are three main components - invocations of dev_net, ip_rcv_core, and NF_HOOK respectively. dev_net is not that interesting for us as it just returns a struct characterizing network namespace from a network device instance. However, the last two parts are more relevant to our investigation. ip_rcv_core's main job is to verify the validity of the packet (by for example checking checksum of the IP header) and in case of successful processing, the updated socket buffer is passed to the netfilter hook (specifically NFHOOK's NF_INET_PRE_ROUTING chain in our case). As the netfilter hook needs to be able to make decisions based on IP headers, I thought that it would be best to trace the return value of ip_rcv_core. That way, we will get access to the fully updated socket buffer and will be able to obtain relevant IP header information. The reason why I haven't chosen to just hook a kprobe for ip_rcv is exactly that the socket buffer will undergo some updates in the ip_rcv_core, so I didn't feel like the socket buffer is fully processed at the entry to the ip_rcv. Tracing just ip_rcv with kretprobe is also problematic because ip_rcv returns an int and not a socket buffer, so we would need to save a pointer to the socket buffer in a BPF hashmap during an ip_rcv kprobe and fetch that pointer afterward in the kretprobe (based on the combination of process and thread ids). However, this approach is problematic as ip_rcv_core might create a new instance of socket buffer, so in kretprobe we could end up fetching an old socket buffer! Therefore, I decided to stick with tracing the return value of ip_rcv_core function. However, because I am not a kernel developer, there might be flaws in my logic, so if anyone has a correction there I would be glad to acommodate it.

So, let's see how a simple kernel trace might look like:

```c
#include <uapi/linux/ip.h>
#include <net/sock.h>
#include <bcc/proto.h>

BPF_PERF_OUTPUT(events);

struct trace_event_data {
    u32 saddr;
    u32 daddr;
    u8 protocol;
};

static inline struct iphdr *skb_to_iphdr(const struct sk_buff *skb)
{
    // unstable API. verify logic in ip_hdr() -> skb_network_header().
    return (struct iphdr *)(skb->head + skb->network_header);
}

int ip_rcv_core_exit(struct pt_regs *ctx) {
	const struct sk_buff *skb = (struct sk_buff *)PT_REGS_RC(ctx);
	if (skb == 0) {
		return 0;	// ip_rcv_core failed
	}

    const struct iphdr *iph = skb_to_iphdr(skb);

    if(iph->protocol == 0x01) {
        struct trace_event_data data = {};     
        data.saddr = iph->saddr;
        data.daddr = iph->daddr;
        data.protocol = iph->protocol;

        events.perf_submit(ctx, &data, sizeof(data));
    }  

	return 0;
}
```

As a first thing, we define a BPF output buffer that will be used to send events to the userspace. We then define a struct trace_event_data that will gather all the important information for the userspace program. Tracer routine just gets the return value of ip_rcv_core and in case it is a non-null socket buffer, it finds the IP header structure inside its data. We then just check if the protocol is 1 (ICMP) and if so, we push the event to the output buffer. Pretty simple, right?

The core of the Python counterpart that will initialize this kretprobe and will parse the output buffer might look like this:

```python
def log_event(cpu, data, size):
    event = b["events"].event(data)

    # Convert binary representations of source and destination IP addresses to their text representations
    src_address = inet_ntop(AF_INET, pack('I', event.saddr))
    dest_address = inet_ntop(AF_INET, pack('I', event.daddr)) 

    print(f'Source: {src_address}; Destination: {dest_address}; Protocol: {event.protocol}.')

if __name__ == '__main__':
    print("Running ip_rcv_core_tracer.c.")

    # Initialize the BPF program
    b = BPF(src_file="ip_rcv_core_tracer.c")

    # Attach ip_rcv_core_exit from ip_rcv_core_tracer.c as a kretprobe probe to the ip_rcv_core
    b.attach_kretprobe(event="ip_rcv_core", fn_name="ip_rcv_core_exit")

    print("Tracing ICMP messages ... Hit Ctrl-C to end")
    b["events"].open_perf_buffer(log_event)
    while True:
        try:
            b.perf_buffer_poll()
        except KeyboardInterrupt:
            print("Exiting.")
            exit()
```

So when I tried running this, the script failed with an error: "Failed to attach BPF to kprobe." It seemed like this function name did not exist, so I checked kernel's system map and found out that there was no ip_rcv_core! However, I saw that there was some ip_rcv_core.isra.20 present. After a quick google search, I found out that gcc compiler can mangle function names when doing optimizations (for an explanation see this [^gcc_mangling_so], see also [^gcc_mangling_bcc_issue] for a discussion on bcc github issues page). So after replacing the argument to attach_kretprobe I was finally able to start the tracer and saw this output when I pinged the machine:

`Source: 192.168.0.115; Destination: 192.168.0.10; Protocol: 1`

Huraaay! As you can see, eBPF looks pretty powerful thanks to the amount of kernel code we are able to trace! And I even haven't talked much about other application of eBPF, like tracing userspace programs, or network drivers! I am not surprised there is so much rush around it nowadays. There is a lot of room for different monitoring and security tooling that can be build on top of eBPF. However, as I was able to witness, writing eBPF programs can be pretty tied to the kernel implementation, which is not API-stable. Therefore, any non-trivial changes in the kernel implementations need to be always propagated to the eBPF programs, which might constitute a non-negligible maintenance cost. Also, it is almost impossible to create production-ready eBPF programs for a software engineer who doesn't have extensive experience with targeted kernel modules. Therefore, I think there is a pretty significant barrier to the extensibility of eBPF programs for non-kernel developers.

If you would like to see the full source code of the ICMP tracer, you can find it here: <https://github.com/ragoragino/ebpf-explorations/tree/master/icmp-tracer>.

Some additional sources about the topic:
* <https://qmo.fr/docs/talk_20190516_allout_programmability_bpf.pdf> (a presentation about classical and extended BPF)
* <https://www.privateinternetaccess.com/blog/linux-networking-stack-from-the-ground-up-part-1/> (a brutally detailed journey of a packet through the Linux networking stack)
* <https://epickrram.blogspot.com/2016/05/navigating-linux-kernel-network-stack_18.html> (a more brain-friendly journey of a packet through the Linux networking stack)
* <https://mcorbin.fr/pages/xdp-introduction/> and <https://duo.com/labs/tech-notes/writing-an-xdp-network-filter-with-ebpf> (some networking filtering examples using express data path, XDP)

Foonotes:

[^tcpdump]: <https://blog.cloudflare.com/bpf-the-forgotten-bytecode/>
[^bcc_install]: <https://github.com/iovisor/bcc/blob/master/INSTALL.md>
[^bcc_kernel_config]: <https://github.com/iovisor/bcc/blob/master/INSTALL.md#kernel-configuration>
[^kprobe]: <https://www.kernel.org/doc/html/latest/trace/kprobes.html>
[^gcc_mangling_so]: <https://stackoverflow.com/questions/18907580/what-is-isra-in-the-kernel-thread-dump/18914402#18914402>
[^gcc_mangling_bcc_issue]: <https://github.com/iovisor/bcc/issues/1754>
[^bpftrace]: <https://github.com/iovisor/bpftrace>
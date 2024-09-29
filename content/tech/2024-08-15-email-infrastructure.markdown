---
layout: post
title:  "Email Security Fundamentals"
date:   2024-08-15 10:00:00 +0100
categories: SoftwareEngineering Miscellaneous
---

Recently, I have been looking a little bit more into the email security space. I found a lot of the information quite dispersed, so thought I will jot down a summary of how email security works in a nutshell. Basically, give an overview of how DKIM, SPF, and DMARC configurations work.

Most of our internet traffic is secured by TLS, where asymmetric public key cryptography ensures that clients connect to the correct servers. The servers however don't need to authenticate/authorize by default the identity of the clients (unless the web server is a bank, a webshop, or something of that sort). However, for emails we want to ensure the identity of both sides - some kind of sender and receiver verification is needed. We don't want to let anyone on the internet be able to send emails pretending to be alice@acorp.com and vice versa, we don't want anyone else but alice@acorp.com to receive the emails destined for that email address. That's why we need a different security setup than the classical web TLS.

Additionally, an important part of the email infrastructure is so-called SMTP servers that handle the email traffic. When alice@acorp.com sends an email to bob@bcorp.com, there would be at least two email servers that would handle those emails (there can be more due to the existence of SMTP relays/forwarding, e.g. see recent [Proofpoint security breach](https://labs.guard.io/echospoofing-a-massive-phishing-campaign-exploiting-proofpoints-email-protection-to-dispatch-3dd6b5417db6)). The SMTP server sending emails for the acorp.com domain that connects to the SMTP server that is tasked with receiving emails for bcorp.com. 

To make things even more complicated, both of the SMTP servers need to decide on what to do if there is any issue with the delivery. What should the receiving (aka inbound) SMTP server do when the email address doesn't exist (aka Bounce message) or the user wants to signal that they don't want to receive such emails (aka Compliant message)? In order to track that information, the sending (aka outbound) server sends a special header called MAIL FROM (aka RETURN PATH) that specifies which email address should be used to send any bounce or compliant messages (as we might not want to send them just back to the original sender, in our case Alice). However, that also means we need to secure this path of the traffic - we need to make sure the sending server can receive the traffic for the RETURN PATH email address.

So, from this setup, it's clear we need multiple security guarantees for email traffic. To summarise, we need to ensure that:

1) inbound server is a legitimate server with permission to handle the traffic for the address in the To (or cc or bcc) header (in our case bob@bcorp.com)
2) outbound server is a legitimate server that can handle the traffic for the domain specified in the RETURN PATH header
3) outbound server is a legitimate server that can send emails for the domain specified in the FROM header
4) that the traffic between inbound and outbound SMTP servers hasn't been tampered with and no other actor (e.g. Mallary) can read the traffic (these are more or less classic TLS concerns)
5) and a final special concern, that the traffic between the original outbound SMTP server and the receiving SMTP server hasn't been tampered in the case of SMTP relays/forwarding servers that can lie in between.

Phuu, that's a lot of security that we need to put into work here!

Let's start with the ones where we can use some existing tools (like TLS or just DNS). 

The 4) can be handled with a simple SSL/TLS and that's what's actually happening (even though there is some complication with how the TLS communication is initiated in some circumstances, see STARTTLS protocol).

The 1) can also be handled in a standard way - by a DNS record. Because we trust the DNS ownership of the domain, we can implicitly trust a special record pointing to a server tasked with handling email traffic. That's the purpose of the MX record - domain owners designate with it the SMTP server for handling inbound email traffic. 

```
acorp.com 1800 MX "10 inbound-smtp.us-east-1.amazonaws.com"
```

By a record like this acorp.com says that AWS's SMTP server in US EAST 1 region can receive traffic on behalf of acorp.com.

The solution to the 2) problem is similar - we can specify a special TXT record that will contain which IP addresses (or potentially other domains) can be used to represent a valid RETURN PATH of the domain. This records takes the form of the so-called Sender Policy Framework and can look like this:

```
acorp.com 1800 TXT "v=spf1 ip4:1.2.3.4 -all"
```

In this example, we are allowing for the IP address of the domain used in the RETURN PATH of the email to be 1.2.3.4.

The solution to the other problem 5) is a separate framework called DKIM, an abbreviation of DomainKeys Identified Mail. We again use the implicit trust in DNS together with public key cryptography. Every SMTP system that wants to protect its outbound emails against tampering, can publish a TXT record against a specified subdomain (composed of the so-called selector and the core `_domainkey.[domain]`) containing a public key. Whenever that server transmits an email to another SMTP server it can sign the email with a private key corresponding to the public key published in that TXT record. In addition, the sending SMTP server adds a special DKIM header containing the selector string and a domain to verify against. The receiving SMTP server can then verify the signature, which effectively guarantees that the email must have been sent by a server associated with the domain in the DKIM header.

Okay, so if we have all of this SPF and SKIM setup, we know that the SMTP server in the RETURN PATH header is valid for receiving bounce and complaints for the domain under that header and that the SMTP server representing the domain in the DKIM header signed the email. However, these two headers have no relationship to the FROM header! So basically, if we only have SPF and DKIM verification, mallory@mcorp.com could still send emails impersonating alice@acorp.com! 

That's where DMARC, or Domain-based Message Authentication, Reporting and Conformance, comes into play. DMARC basically ties DKIM and SPF protocols with the FROM header and allows specifying policies in case any of these protocols fail. The DMARC record is another DNS record that is added to the domain and that specifies what kind of DKIM and SPF alignment we expect and what to do if the DMARC checks fail for an email.

DMARC DNS record is looked up in the domain associated with the FROM header. This domain is then checked against the RETURN PATH (for SPF alignment), and against the domain in the DKIM header (for DKIM alignment). The inbound server can then based on the DMARC DNS record decide what do to if these domains don't align (either in a strict or relaxed manner, see [this article](https://support.google.com/a/answer/10032169?sjid=6770605781840214383-EU#dmarc-alignment) for the difference).

```
v=DMARC1; p=reject; adkim=s; aspf=s;
```

For example, a record like this would ensure that SPF MAIL FROM and DKIM FROM header have to be from the same domain as the FROM header. If that's not the case, the inbound SMTP server can reject the message.

With all of this setup now, we can be sure the domain of the outbound email address did actually send the email!

So, to recap, let's write down the solutions to each of the security problems:

1) inbound server is a legitimate server with permission to handle the traffic for bcorp.com: **DNS MX**
2) outbound server is a legitimate server that can handle the traffic for the domain specified in the RETURN PATH header: **SPF**
3) outbound server is a legitimate server that can send emails to the domain specified in the FROM header: **DMARC**
4) the traffic between inbound and outbound SMTP servers hasn't been tampered with and no other actor (e.g. Mallary) can read the traffic (these are more or less classic TLS concerns): **TLS**
5) and a final special concern, that the traffic between the original outbound SMTP server and the receiving SMTP server hasn't been tampered in the case of SMTP relays/forwarding servers that can lie in between: **DKIM**
# Keep Fatties on the web and use Scriptable for the iPhone widget

Fatties remains the existing web app, with private retained meal photos in Cloudflare R2 and a narrowly authenticated partner-last-log feed. Both partners install the existing Scriptable app to display a medium Home Screen widget from that feed. The user selected this on 9 November 2026 to avoid maintaining and distributing a dedicated native Fatties app; the trade-offs are a one-time Scriptable setup, dependence on that host, and periodic iOS-controlled refresh rather than guaranteed immediate updates.

Dedicated native wrappers, paid Apple Developer membership, Ad Hoc signing, TestFlight distribution, and custom APNs widget updates are outside this architecture. Tapping opens the meal's web URL; whether iOS opens Safari or the installed web app must be tested on device.

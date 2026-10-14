# python3 setup.py py2app  ->  dist/ZTE Helper.app
from setuptools import setup

setup(
    app=["zte_app.py"],
    name="ZTE Helper",
    data_files=[("", ["../zte-script-legacy.js", "../mikrotik-script.js"])],
    options={"py2app": {
        "packages": ["webview"],
        "plist": {
            "CFBundleName": "ZTE Helper",
            "CFBundleIdentifier": "com.boyansoft.zte-helper",
            # router UI is plain http on the LAN
            "NSAppTransportSecurity": {"NSAllowsArbitraryLoadsInWebContent": True, "NSAllowsLocalNetworking": True},
            "NSLocalNetworkUsageDescription": "Connects to your ZTE and MikroTik routers on the local network.",
        },
    }},
    setup_requires=["py2app"],
)

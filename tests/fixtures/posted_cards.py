"""Real Discord webhook payloads captured from production (pre-round-49).

Each entry is the EXACT ordered list of media-gallery tile urls that Discord
accepted and rendered, plus the container split that produced them. Used to
replay round 49 against known-good output.
"""
I = "https://i.redd.it/"
P = "https://preview.redd.it/"
EZ = ("https://embedez.com/api/v2/redirect/search_6abb21a5bc35c687ecc157fb"
      "?path=content.media.%d.source")

POSTED_CARDS = {
    "1wt8hdd": {"split": [7], "urls": [
        I + "iufxauvv9gsh1.jpg",
        P + "m4p5c7ggagsh1.png?width=4318&format=png&auto=webp&s=4afcc588e64c58d9b5e7a2843f86ce1e25758756",
        P + "2kg28c6hagsh1.png?width=4318&format=png&auto=webp&s=9da6c84f9e7f8ed98fbd20572d52611bf746f20a",
        P + "kjupl0pjagsh1.png?width=1440&format=png&auto=webp&s=8e70c5624bcc1e01ee745eb4819ee35250663760",
        P + "eqzwp85kagsh1.png?width=1440&format=png&auto=webp&s=616fa729dd0b1a8e67bdfbb1d31abee871ceed32",
        P + "u4x4zejkagsh1.png?width=1440&format=png&auto=webp&s=e2161bca40e7f5994e9561503ffe19c8c9ad3528",
        P + "53ur6rwkagsh1.png?width=1440&format=png&auto=webp&s=43eaa41a30fb7b8972cdd63091c7c547f5b84dbb",
    ]},
    "1woqtu9": {"split": [7], "urls": [
        I + "y48c2ov80erh1.jpg",
        P + "udu9zgc90erh1.png?width=1280&format=png&auto=webp&s=2b2f7a011c08af511ff944b4330651d6dbd81c52",
        P + "dymnq1w90erh1.png?width=946&format=png&auto=webp&s=5e06caae5d0dfe02fd8dee33b8040185ca87df8b",
        P + "ez3v7m5a0erh1.png?width=1280&format=png&auto=webp&s=af98e847c2121a5c02b79e470a9c724f3101a091",
        P + "kidk5cfa0erh1.png?width=1280&format=png&auto=webp&s=f3035bca52a7657d474364ba78d73f0e341e8304",
        P + "jdfap7oa0erh1.png?width=1280&format=png&auto=webp&s=17b0cf2f8dfe15e745f2a06459f9c68bc1a45d30",
        P + "chuavrwc0erh1.png?width=430&format=png&auto=webp&s=87fcff9325902a5359469bbb784c22b29402728e",
    ]},
    "1wsy39s": {"split": [6], "urls": [EZ % i for i in range(6)]},
    "1wqkq60": {"split": [10, 5], "urls": [I + n for n in (
        "njbmltmxltrh1.jpg", "44kleumxltrh1.jpg", "1pyeqwmxltrh1.gif",
        "7a6q1vmxltrh1.gif", "p70i4vmxltrh1.gif", "yi6powmxltrh1.gif",
        "bswrxvmxltrh1.gif", "8sj76vmxltrh1.gif", "t1cv6wmxltrh1.gif",
        "bzn4avmxltrh1.gif", "7cr3ovmxltrh1.gif", "rjxw1umxltrh1.gif",
        "k3ignumxltrh1.gif", "h5hw7ymxltrh1.jpg", "ddmq4umxltrh1.jpg")]},
    "1wmxt0l": {"split": [10, 5], "urls": [I + n for n in (
        "4s26ict0mzqh1.jpg", "xa7iurx0mzqh1.jpg", "3ejdoj11mzqh1.gif",
        "0hqhq582mzqh1.gif", "vsa6ysa3mzqh1.gif", "v4hjris4mzqh1.gif",
        "x5egva46mzqh1.gif", "13y71y27mzqh1.gif", "ic5k73h8mzqh1.gif",
        "jrwa97b9mzqh1.gif", "gd8r4hmamzqh1.gif", "uhj86skbmzqh1.jpg",
        "5zrzptnbmzqh1.jpg", "12d8jhqbmzqh1.jpg", "i3xt91tbmzqh1.jpg")]},
    "1wov3r0": {"split": [10, 5], "urls": [I + n + ".jpg" for n in (
        "frm0hl747frh1", "tbs8tk747frh1", "0vtd5l747frh1", "07p9pl747frh1",
        "ikdirk747frh1", "0hwbll747frh1", "vr3nxk747frh1", "1n7ydl747frh1",
        "r2onnk747frh1", "4z3yvl747frh1", "7w8ttk747frh1", "vsxe6l747frh1",
        "y0qedl747frh1", "6vrwkl747frh1", "4ne4zk747frh1")]},
    "1wp17bk": {"split": [10, 5], "urls": [I + n + ".jpg" for n in (
        "21vvvav9wgrh1", "lm2imbv9wgrh1", "94xz4cv9wgrh1", "6t9drbv9wgrh1",
        "4vbfrcv9wgrh1", "xf402bv9wgrh1", "6onb3dv9wgrh1", "yofq0cv9wgrh1",
        "agwhfcv9wgrh1", "gl6c3bv9wgrh1", "tjih5cv9wgrh1", "d8172cv9wgrh1",
        "vjy76cv9wgrh1", "mvb00mv9wgrh1", "dc4tgdv9wgrh1")]},
    "1wtg5r3": {"split": [10, 9], "urls": [I + n + ".png" for n in (
        "mt7f94pr3hsh1", "3ye0s5pr3hsh1", "7g7005pr3hsh1", "3mmx65pr3hsh1",
        "3o9ew3pr3hsh1", "kdwu53pr3hsh1", "sb4mn3pr3hsh1", "p2fsc4pr3hsh1",
        "hvaoa4pr3hsh1", "vg1hz3pr3hsh1", "pul9j4pr3hsh1", "ynhcm5pr3hsh1",
        "go51g3pr3hsh1", "i4w5t3pr3hsh1", "e0nlz3pr3hsh1", "bl04g5pr3hsh1",
        "7nrfo4pr3hsh1", "7e6ho3pr3hsh1", "vskxo5pr3hsh1")]},
    "1wkj08n": {"split": [10, 10], "urls": [I + n + ".jpg" for n in (
        "9evy7bnhigqh1", "txsqa6shigqh1", "6rx5qlwhigqh1", "6u3p4b1iigqh1",
        "d59jut5iigqh1", "84bo488iigqh1", "8fn6vyaiigqh1", "tmhwyhdiigqh1",
        "4bpm5yfiigqh1", "9dlufgiiigqh1", "qd2bveliigqh1", "f671pwniigqh1",
        "zd0xmhqiigqh1", "xdmuxmsiigqh1", "ocwqtaviigqh1", "2t3vyhxiigqh1",
        "cp9scvziigqh1", "yyluwp2jigqh1", "4xuqtw4jigqh1", "7s2oae7jigqh1")]},
}

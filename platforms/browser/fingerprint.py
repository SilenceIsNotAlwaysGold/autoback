"""账号级指纹生成器 — 每个账号独立且稳定的浏览器指纹

基于账号名的稳定摘要生成指纹参数，同一账号每次启动保持一致，
不同账号产生稳定差异，同时确保平台字段与当前操作系统一致。
"""
import hashlib
import sys


# WebGL Vendor + Renderer 组合（真实设备数据）
WEBGL_CONFIGS = [
    ("Intel Inc.", "Intel Iris OpenGL Engine"),
    ("Intel Inc.", "Intel Iris Plus Graphics 640"),
    ("Intel Inc.", "Intel UHD Graphics 630"),
    ("Intel Inc.", "Intel HD Graphics 530"),
    ("Apple", "Apple M1"),
    ("Apple", "Apple M2"),
    ("Apple", "Apple M3"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA GeForce GTX 1060)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA GeForce RTX 3060)"),
    ("Google Inc. (AMD)", "ANGLE (AMD Radeon RX 580)"),
    ("Google Inc. (Intel)", "ANGLE (Intel UHD Graphics 620)"),
    ("Google Inc. (Intel)", "ANGLE (Intel Iris Xe Graphics)"),
]

# 平台 + hardwareConcurrency 组合
PLATFORMS = [
    ("MacIntel", 8),
    ("MacIntel", 10),
    ("MacIntel", 12),
    ("Win32", 4),
    ("Win32", 8),
    ("Win32", 12),
    ("Win32", 16),
    ("Linux x86_64", 4),
    ("Linux x86_64", 8),
]

# deviceMemory 选项
MEMORY_OPTIONS = [4, 8, 16, 32]

# 屏幕分辨率
SCREEN_RESOLUTIONS = [
    (1440, 900), (1536, 864), (1920, 1080),
    (1366, 768), (1280, 800), (1600, 900),
    (2560, 1440), (1680, 1050), (1440, 1080),
]


def _seed(account_name: str) -> int:
    """账号名 → 稳定的数字种子"""
    return int(hashlib.sha256(account_name.encode()).hexdigest(), 16)


def _platform_options() -> list[tuple[str, int]]:
    """只生成与宿主系统一致的平台字段，避免 UA/平台交叉穿帮。"""
    if sys.platform == "darwin":
        return [item for item in PLATFORMS if item[0] == "MacIntel"]
    if sys.platform == "win32":
        return [item for item in PLATFORMS if item[0] == "Win32"]
    return [item for item in PLATFORMS if item[0] == "Linux x86_64"]


def _webgl_options() -> list[tuple[str, str]]:
    if sys.platform == "darwin":
        return [item for item in WEBGL_CONFIGS if item[0] in ("Intel Inc.", "Apple")]
    return [item for item in WEBGL_CONFIGS if item[0].startswith("Google Inc.")]


def generate_fingerprint(account_name: str) -> dict:
    """为账号生成唯一且稳定的指纹配置

    Returns:
        {
            "webgl_vendor": "...",
            "webgl_renderer": "...",
            "platform": "MacIntel",
            "hardware_concurrency": 8,
            "device_memory": 8,
            "screen_width": 1920,
            "screen_height": 1080,
            "canvas_noise": 0.03,  # Canvas 噪声系数
            "audio_noise": 0.0001,
            "timezone_offset": -480,  # UTC+8
        }
    """
    seed = _seed(account_name)

    webgl_options = _webgl_options() or WEBGL_CONFIGS
    platform_options = _platform_options() or PLATFORMS
    webgl = webgl_options[seed % len(webgl_options)]
    platform = platform_options[(seed >> 8) % len(platform_options)]
    memory = MEMORY_OPTIONS[(seed >> 16) % len(MEMORY_OPTIONS)]
    screen = SCREEN_RESOLUTIONS[(seed >> 24) % len(SCREEN_RESOLUTIONS)]
    canvas_noise = 0.01 + (seed % 100) / 1000  # 0.01 ~ 0.11

    return {
        "webgl_vendor": webgl[0],
        "webgl_renderer": webgl[1],
        "platform": platform[0],
        "hardware_concurrency": platform[1],
        "device_memory": memory,
        "screen_width": screen[0],
        "screen_height": screen[1],
        "canvas_noise": round(canvas_noise, 4),
        "audio_noise": round(0.0001 + (seed % 50) / 100000, 6),
        "timezone_offset": -480,
    }


def generate_stealth_script(account_name: str) -> str:
    """生成账号专属的反检测 JS 脚本"""
    fp = generate_fingerprint(account_name)
    canvas_seed = _seed(account_name) & 0xFFFFFFFF
    noise_step = max(1, min(2, round(fp["canvas_noise"] * 16)))

    return f"""
// === 账号专属指纹: {account_name} ===

// 隐藏 webdriver
Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
delete navigator.__proto__.webdriver;

// 隐藏 Playwright
delete window.__playwright;
delete window.__pw_manual;

// 伪造 plugins
Object.defineProperty(navigator, 'plugins', {{
    get: () => {{
        const plugins = [
            {{ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' }},
            {{ name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' }},
            {{ name: 'Native Client', filename: 'internal-nacl-plugin' }},
        ];
        plugins.length = 3;
        return plugins;
    }}
}});

// 语言
Object.defineProperty(navigator, 'languages', {{ get: () => ['zh-CN', 'zh', 'en-US', 'en'] }});

// 平台（账号专属）
Object.defineProperty(navigator, 'platform', {{ get: () => '{fp["platform"]}' }});

// 硬件（账号专属）
Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {fp["hardware_concurrency"]} }});
Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {fp["device_memory"]} }});

// 屏幕（账号专属）
Object.defineProperty(screen, 'width', {{ get: () => {fp["screen_width"]} }});
Object.defineProperty(screen, 'height', {{ get: () => {fp["screen_height"]} }});
Object.defineProperty(screen, 'availWidth', {{ get: () => {fp["screen_width"]} }});
Object.defineProperty(screen, 'availHeight', {{ get: () => {fp["screen_height"] - 40} }});

// Permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
        ? Promise.resolve({{ state: Notification.permission }})
        : originalQuery(parameters)
);

// Canvas 指纹噪声：账号内确定性一致，不修改原始 Canvas
const _toBlob = HTMLCanvasElement.prototype.toBlob;
const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
const FINGERPRINT_SEED = {canvas_seed};
const NOISE_STEP = {noise_step};

function deterministicNoise(index, channel) {{
    let x = (FINGERPRINT_SEED
        ^ Math.imul(index + 1, 0x45d9f3b)
        ^ Math.imul(channel + 1, 0x27d4eb2d)) >>> 0;
    x = Math.imul(x ^ (x >>> 16), 0x45d9f3b) >>> 0;
    x = Math.imul(x ^ (x >>> 16), 0x45d9f3b) >>> 0;
    return ((x ^ (x >>> 16)) % 3) - 1;
}}

function noisyCanvas(source) {{
    try {{
        const clone = document.createElement('canvas');
        clone.width = source.width;
        clone.height = source.height;
        const ctx = clone.getContext('2d');
        if (!ctx) return source;
        ctx.drawImage(source, 0, 0);
        const w = Math.min(clone.width, 16);
        const h = Math.min(clone.height, 16);
        if (!w || !h) return clone;
        const imageData = ctx.getImageData(0, 0, w, h);
        for (let i = 0; i < imageData.data.length; i += 4) {{
            imageData.data[i] = Math.max(0, Math.min(
                255, imageData.data[i] + deterministicNoise(i, 0) * NOISE_STEP
            ));
            imageData.data[i + 1] = Math.max(0, Math.min(
                255, imageData.data[i + 1] + deterministicNoise(i, 1) * NOISE_STEP
            ));
            imageData.data[i + 2] = Math.max(0, Math.min(
                255, imageData.data[i + 2] + deterministicNoise(i, 2) * NOISE_STEP
            ));
        }}
        ctx.putImageData(imageData, 0, 0);
        return clone;
    }} catch (_) {{
        return source;
    }}
}}

HTMLCanvasElement.prototype.toBlob = function(cb, type, quality) {{
    return _toBlob.call(noisyCanvas(this), cb, type, quality);
}};

HTMLCanvasElement.prototype.toDataURL = function(type, quality) {{
    return _toDataURL.call(noisyCanvas(this), type, quality);
}};

// WebGL 指纹（账号专属）
const _getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {{
    if (param === 37445) return '{fp["webgl_vendor"]}';
    if (param === 37446) return '{fp["webgl_renderer"]}';
    return _getParameter.call(this, param);
}};

// WebGL2
if (typeof WebGL2RenderingContext !== 'undefined') {{
    const _getParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(param) {{
        if (param === 37445) return '{fp["webgl_vendor"]}';
        if (param === 37446) return '{fp["webgl_renderer"]}';
        return _getParameter2.call(this, param);
    }};
}}
"""

"""账号级指纹生成器 — 每个账号独立且稳定的浏览器指纹

基于账号名 hash 生成所有指纹参数，同一账号每次启动指纹一致，
不同账号指纹完全不同，平台无法关联。
"""
import hashlib
import json


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

    webgl = WEBGL_CONFIGS[seed % len(WEBGL_CONFIGS)]
    platform = PLATFORMS[(seed >> 8) % len(PLATFORMS)]
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

// Canvas 指纹噪声（账号专属噪声系数）
const _toBlob = HTMLCanvasElement.prototype.toBlob;
const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
const NOISE = {fp["canvas_noise"]};

HTMLCanvasElement.prototype.toBlob = function(cb, type, quality) {{
    const ctx = this.getContext('2d');
    if (ctx) {{
        const w = Math.min(this.width, 16);
        const h = Math.min(this.height, 16);
        const imageData = ctx.getImageData(0, 0, w, h);
        for (let i = 0; i < imageData.data.length; i += 4) {{
            imageData.data[i] = Math.max(0, Math.min(255, imageData.data[i] + Math.floor((Math.random() - 0.5) * NOISE * 255)));
        }}
        ctx.putImageData(imageData, 0, 0);
    }}
    return _toBlob.call(this, cb, type, quality);
}};

HTMLCanvasElement.prototype.toDataURL = function(type, quality) {{
    const ctx = this.getContext('2d');
    if (ctx) {{
        const w = Math.min(this.width, 16);
        const h = Math.min(this.height, 16);
        const imageData = ctx.getImageData(0, 0, w, h);
        for (let i = 0; i < imageData.data.length; i += 4) {{
            imageData.data[i] = Math.max(0, Math.min(255, imageData.data[i] + Math.floor((Math.random() - 0.5) * NOISE * 255)));
        }}
        ctx.putImageData(imageData, 0, 0);
    }}
    return _toDataURL.call(this, type, quality);
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

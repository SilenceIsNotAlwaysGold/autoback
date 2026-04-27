"""反检测脚本集合

集成 Phantom 反检测思路 + 社区最佳实践。
三平台共享。
"""

# 基础反检测（所有平台必须）
STEALTH_BASIC = """
// 隐藏 webdriver 标记
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
delete navigator.__proto__.webdriver;

// 伪造 plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' },
        ];
        plugins.length = 3;
        return plugins;
    }
});

// 伪造 languages
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });

// 伪造 platform
Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });

// 伪造 hardwareConcurrency
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

// 修复 permissions query
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
);

// 隐藏 Playwright 签名
delete window.__playwright;
delete window.__pw_manual;
"""

# Canvas 指纹噪声（防止指纹追踪）
STEALTH_CANVAS = """
const _toBlob = HTMLCanvasElement.prototype.toBlob;
const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
const _getImageData = CanvasRenderingContext2D.prototype.getImageData;

HTMLCanvasElement.prototype.toBlob = function(cb, type, quality) {
    const ctx = this.getContext('2d');
    if (ctx) {
        const pixel = ctx.getImageData(0, 0, 1, 1);
        pixel.data[3] = pixel.data[3] > 0 ? pixel.data[3] - 1 : 1;
        ctx.putImageData(pixel, 0, 0);
    }
    return _toBlob.call(this, cb, type, quality);
};

HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
    const ctx = this.getContext('2d');
    if (ctx) {
        const pixel = ctx.getImageData(0, 0, 1, 1);
        pixel.data[3] = pixel.data[3] > 0 ? pixel.data[3] - 1 : 1;
        ctx.putImageData(pixel, 0, 0);
    }
    return _toDataURL.call(this, type, quality);
};
"""

# WebGL 指纹伪装
STEALTH_WEBGL = """
const _getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Intel Inc.';
    if (param === 37446) return 'Intel Iris OpenGL Engine';
    return _getParameter.call(this, param);
};
"""

# 完整反检测脚本（合并所有）
STEALTH_FULL = STEALTH_BASIC + STEALTH_CANVAS + STEALTH_WEBGL

# User-Agent 池（来自 jogholy/xhs-publisher 的 13 种 Chrome 版本）
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# 视口尺寸池
VIEWPORTS = [
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 800},
    {"width": 1600, "height": 900},
]

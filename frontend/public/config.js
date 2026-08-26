// 后端地址：由部署方注入 window.LM_BACKEND（详见 src/utils/common.js 的解析优先级）
// · 后端单端口(5679)统一托管时：本文件 = http(s)://本机host:5679，与页面同源
// · 独立前端服务(3000/3001)时：tools/FrontendServer.java 会动态覆盖本文件，指向后端(5678/5679)
window.LM_BACKEND = location.origin;
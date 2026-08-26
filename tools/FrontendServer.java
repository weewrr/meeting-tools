import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import com.sun.net.httpserver.HttpsConfigurator;
import com.sun.net.httpserver.HttpsServer;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.util.Map;
import java.util.concurrent.Executors;

/**
 * 轻会议 LiteMeet - 前端静态服务器（单文件，JDK 21 原生实现，零依赖）
 *
 * 运行：java tools/FrontendServer.java [--root frontend] [--http 3000] [--https 3001]
 *                                        [--backend-http-port 5678] [--backend-https-port 5679]
 *                                        [--keystore data/https-keystore.p12]
 *
 * - HTTP 端口：本机访问（localhost 为安全上下文，摄像头/麦克风可用）
 * - HTTPS 端口：局域网设备访问（复用后端生成的自签名 keystore）
 * - /config.js 动态注入后端地址（按访问协议/主机名推导，前端跨域对接后端 API + WebSocket）
 */
public class FrontendServer {

    private static final Map<String, String> MIME = Map.ofEntries(
            Map.entry("html", "text/html; charset=utf-8"),
            Map.entry("js", "application/javascript; charset=utf-8"),
            Map.entry("mjs", "application/javascript; charset=utf-8"),
            Map.entry("css", "text/css; charset=utf-8"),
            Map.entry("json", "application/json; charset=utf-8"),
            Map.entry("png", "image/png"),
            Map.entry("jpg", "image/jpeg"),
            Map.entry("jpeg", "image/jpeg"),
            Map.entry("gif", "image/gif"),
            Map.entry("svg", "image/svg+xml"),
            Map.entry("ico", "image/x-icon"),
            Map.entry("wav", "audio/wav"),
            Map.entry("mp3", "audio/mpeg"),
            Map.entry("webm", "audio/webm"),
            Map.entry("woff", "font/woff"),
            Map.entry("woff2", "font/woff2"),
            Map.entry("txt", "text/plain; charset=utf-8"));

    private final Path root;
    private final int backendHttpPort;
    private final int backendHttpsPort;
    private final String keystorePassword;

    private FrontendServer(Path root, int backendHttpPort, int backendHttpsPort, String keystorePassword) {
        this.root = root;
        this.backendHttpPort = backendHttpPort;
        this.backendHttpsPort = backendHttpsPort;
        this.keystorePassword = keystorePassword;
    }

    public static void main(String[] args) throws Exception {
        Map<String, String> opt = parseArgs(args);
        Path root = Path.of(opt.getOrDefault("root", "frontend"));
        int httpPort = Integer.parseInt(opt.getOrDefault("http", "3000"));
        int httpsPort = Integer.parseInt(opt.getOrDefault("https", "3001"));
        int backendHttp = Integer.parseInt(opt.getOrDefault("backend-http-port", "5678"));
        int backendHttps = Integer.parseInt(opt.getOrDefault("backend-https-port", "5679"));
        Path keystore = Path.of(opt.getOrDefault("keystore", "data/https-keystore.p12"));
        String keystorePass = opt.getOrDefault("keystore-pass", "litemeet");

        if (!Files.isDirectory(root)) {
            System.err.println("前端目录不存在: " + root.toAbsolutePath());
            System.exit(1);
        }

        FrontendServer server = new FrontendServer(root, backendHttp, backendHttps, keystorePass);

        // HTTP
        HttpServer http = HttpServer.create(new InetSocketAddress(httpPort), 0);
        http.createContext("/", server::handle);
        http.setExecutor(Executors.newVirtualThreadPerTaskExecutor());
        http.start();
        System.out.println("前端服务 (HTTP):  http://localhost:" + httpPort);

        // HTTPS（等后端生成 keystore，最多 60 秒）
        SSLContext ssl = waitAndLoadSsl(keystore, keystorePass);
        if (ssl != null) {
            HttpsServer https = HttpsServer.create(new InetSocketAddress(httpsPort), 0);
            https.setHttpsConfigurator(new HttpsConfigurator(ssl));
            https.createContext("/", server::handle);
            https.setExecutor(Executors.newVirtualThreadPerTaskExecutor());
            https.start();
            System.out.println("前端服务 (HTTPS): https://localhost:" + httpsPort
                    + "  （局域网设备请用此协议访问）");
        } else {
            System.out.println("HTTPS 未启用（未找到 keystore: " + keystore + "）");
        }
        System.out.println("后端 API:        http://localhost:" + backendHttp
                + "  (Swagger 文档: /swagger-ui.html)");
    }

    // ---------- 请求处理 ----------

    private void handle(HttpExchange exchange) throws IOException {
        try {
            String path = exchange.getRequestURI().getPath();
            if (path.equals("/config.js")) {
                serveConfig(exchange);
                return;
            }
            if (path.equals("/") || path.isEmpty()) path = "/index.html";
            // 防目录穿越 + 解析文件
            Path file = root.resolve(path.substring(1)).normalize();
            if (!file.startsWith(root) || !Files.isRegularFile(file)) {
                // SPA 式回退：无后缀的未知路径回首页
                if (!path.contains(".")) {
                    file = root.resolve("index.html");
                    if (!Files.isRegularFile(file)) {
                        send404(exchange);
                        return;
                    }
                } else {
                    send404(exchange);
                    return;
                }
            }
            byte[] data = Files.readAllBytes(file);
            String mime = MIME.getOrDefault(ext(file.getFileName().toString()),
                    "application/octet-stream");
            exchange.getResponseHeaders().set("Content-Type", mime);
            exchange.getResponseHeaders().set("Cache-Control", "no-cache");
            exchange.sendResponseHeaders(200, data.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(data);
            }
        } catch (Exception e) {
            send404(exchange);
        }
    }

    /** 动态注入后端地址：按访问协议/主机名推导，保证同机/局域网都指向正确后端端口 */
    private void serveConfig(HttpExchange ex) throws IOException {
        String js = "// 由前端服务器动态生成（勿手动修改）\n"
                + "window.LM_BACKEND = location.protocol === 'https:'\n"
                + "  ? ('https://' + location.hostname + ':" + backendHttpsPort + "')\n"
                + "  : ('http://' + location.hostname + ':" + backendHttpPort + "');\n";
        byte[] data = js.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/javascript; charset=utf-8");
        ex.getResponseHeaders().set("Cache-Control", "no-store");
        ex.sendResponseHeaders(200, data.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(data);
        }
    }

    private void send404(HttpExchange ex) throws IOException {
        byte[] data = "Not Found".getBytes(StandardCharsets.UTF_8);
        ex.sendResponseHeaders(404, data.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(data);
        }
    }

    // ---------- SSL ----------

    private static SSLContext waitAndLoadSsl(Path keystore, String password) throws Exception {
        // 最多等 60 秒（后端首次启动时生成 keystore 需要几秒）
        for (int i = 0; i < 60; i++) {
            if (Files.exists(keystore)) {
                SSLContext ctx = loadSsl(keystore, password);
                if (ctx != null) return ctx;
            }
            Thread.sleep(1000);
        }
        return null;
    }

    private static SSLContext loadSsl(Path keystore, String password) {
        try (InputStream in = Files.newInputStream(keystore)) {
            KeyStore ks = KeyStore.getInstance("PKCS12");
            ks.load(in, password.toCharArray());
            KeyManagerFactory kmf = KeyManagerFactory.getInstance(
                    KeyManagerFactory.getDefaultAlgorithm());
            kmf.init(ks, password.toCharArray());
            SSLContext ctx = SSLContext.getInstance("TLS");
            ctx.init(kmf.getKeyManagers(), null, null);
            return ctx;
        } catch (Exception e) {
            System.err.println("加载 keystore 失败: " + e.getMessage());
            return null;
        }
    }

    // ---------- 工具 ----------

    private static String ext(String name) {
        int idx = name.lastIndexOf('.');
        return idx < 0 ? "" : name.substring(idx + 1).toLowerCase();
    }

    private static Map<String, String> parseArgs(String[] args) {
        Map<String, String> opt = new java.util.HashMap<>();
        for (int i = 0; i < args.length; i++) {
            String a = args[i];
            if (a.startsWith("--") && i + 1 < args.length) {
                opt.put(a.substring(2), args[++i]);
            }
        }
        return opt;
    }
}

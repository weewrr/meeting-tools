package com.litemeet.backend.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.ArrayList;
import java.util.List;

/**
 * 自签名证书管理
 * 用 JDK 自带 keytool 生成 PKCS12 keystore（含本机所有局域网 IP 的 SAN）
 * keystore 同时供独立的前端静态服务器复用（HTTPS 端口）
 */
public class CertManager {

    private static final Logger log = LoggerFactory.getLogger(CertManager.class);
    private static final String KEYSTORE_PASSWORD = "litemeet";
    private static final String ALIAS = "litemeet";

    private final Path dataDir;
    private final ObjectMapper mapper = new ObjectMapper();

    public CertManager(Path dataDir) {
        this.dataDir = dataDir;
    }

    public Path keystorePath() {
        return dataDir.resolve("https-keystore.p12");
    }

    public String keystorePassword() {
        return KEYSTORE_PASSWORD;
    }

    /** 确保证书存在且覆盖当前所有局域网 IP；返回 keystore 路径 */
    public Path ensureKeystore() {
        try {
            Files.createDirectories(dataDir);
        } catch (IOException e) {
            log.warn("无法创建数据目录: {}", e.getMessage());
            return null;
        }
        List<String> ips = lanIPv4s();
        Path keystore = keystorePath();
        Path meta = dataDir.resolve("https-cert-meta.json");

        // 缓存有效则复用（IP 集合变化时重新生成）
        try {
            if (Files.exists(keystore) && Files.exists(meta)) {
                List<String> saved = new ArrayList<>();
                var node = mapper.readTree(Files.readString(meta, StandardCharsets.UTF_8));
                node.path("ips").forEach(n -> saved.add(n.asText()));
                if (saved.size() == ips.size() && ips.containsAll(saved)) {
                    return keystore;
                }
            }
        } catch (Exception ignored) { /* 重新生成 */ }

        // 用 keytool 生成（JDK 自带）
        try {
            List<String> command = new ArrayList<>(List.of(
                    "keytool", "-genkeypair",
                    "-alias", ALIAS,
                    "-keyalg", "RSA", "-keysize", "2048",
                    "-validity", "3650",
                    "-dname", "CN=LiteMeet",
                    "-keystore", keystore.toString(),
                    "-storetype", "PKCS12",
                    "-storepass", KEYSTORE_PASSWORD,
                    "-noprompt"));
            StringBuilder san = new StringBuilder("SAN=dns:localhost,ip:127.0.0.1");
            for (String ip : ips) san.append(",ip:").append(ip);
            command.add("-ext");
            command.add(san.toString());

            Files.deleteIfExists(keystore);
            Process p = new ProcessBuilder(command)
                    .redirectErrorStream(true)
                    .start();
            String output = new String(p.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            int code = p.waitFor();
            if (code != 0 || !Files.exists(keystore)) {
                log.warn("keytool 生成证书失败（退出码 {}）: {}", code, output.trim());
                return null;
            }
            // 写 meta
            var root = mapper.createObjectNode();
            var arr = root.putArray("ips");
            ips.forEach(arr::add);
            Files.writeString(meta, mapper.writerWithDefaultPrettyPrinter()
                    .writeValueAsString(root), StandardCharsets.UTF_8);
            log.info("已生成自签名证书（有效期 10 年，SAN: {}）", san);
            return keystore;
        } catch (Exception e) {
            log.warn("证书生成失败: {}", e.getMessage());
            return null;
        }
    }

    /** 本机所有局域网 IPv4 */
    public static List<String> lanIPv4s() {
        List<String> ips = new ArrayList<>();
        try {
            var interfaces = NetworkInterfaceGetter.get();
            for (var ni : interfaces) {
                for (var addr : ni.getInterfaceAddresses()) {
                    var ia = addr.getAddress();
                    if (ia instanceof java.net.Inet4Address && !ia.isLoopbackAddress()) {
                        ips.add(ia.getHostAddress());
                    }
                }
            }
        } catch (Exception ignored) { }
        return ips;
    }

    /** 小包装：隔离 NetworkInterface 的受检异常 */
    static final class NetworkInterfaceGetter {
        static List<java.net.NetworkInterface> get() throws Exception {
            List<java.net.NetworkInterface> list = new ArrayList<>();
            java.net.NetworkInterface.getNetworkInterfaces().asIterator()
                    .forEachRemaining(list::add);
            return list;
        }
    }
}

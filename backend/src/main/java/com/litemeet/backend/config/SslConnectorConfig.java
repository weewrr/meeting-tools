package com.litemeet.backend.config;

import org.apache.catalina.connector.Connector;
import org.apache.tomcat.util.net.SSLHostConfig;
import org.apache.tomcat.util.net.SSLHostConfigCertificate;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.embedded.tomcat.TomcatServletWebServerFactory;
import org.springframework.boot.web.server.WebServerFactoryCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.nio.file.Path;

/**
 * 附加 HTTPS 连接器（局域网设备访问音视频必需）
 * 主端口 5678（HTTP），附加端口 5679（HTTPS，自签名证书）
 * Tomcat 10.1 需通过 SSLHostConfig 对象配置证书
 */
@Configuration
public class SslConnectorConfig {

    private static final Logger log = LoggerFactory.getLogger(SslConnectorConfig.class);

    @Value("${litemeet.https-port:5679}")
    private int httpsPort;

    @Value("${litemeet.data-dir:data}")
    private String dataDir;

    @Bean
    public WebServerFactoryCustomizer<TomcatServletWebServerFactory> httpsConnectorCustomizer() {
        return factory -> {
            Path keystore = new CertManager(Path.of(dataDir)).ensureKeystore();
            if (keystore == null) {
                log.warn("HTTPS 端口未启用（证书生成失败）");
                return;
            }

            Connector connector = new Connector(TomcatServletWebServerFactory.DEFAULT_PROTOCOL);
            connector.setScheme("https");
            connector.setPort(httpsPort);
            connector.setSecure(true);

            SSLHostConfig sslHostConfig = new SSLHostConfig();
            sslHostConfig.setHostName("_default_");
            SSLHostConfigCertificate certificate = new SSLHostConfigCertificate(
                    sslHostConfig, SSLHostConfigCertificate.Type.UNDEFINED);
            certificate.setCertificateKeystoreFile(keystore.toAbsolutePath().toString());
            certificate.setCertificateKeystorePassword("litemeet");
            certificate.setCertificateKeystoreType("PKCS12");
            sslHostConfig.addCertificate(certificate);
            connector.addSslHostConfig(sslHostConfig);
            connector.setProperty("SSLEnabled", "true");

            factory.addAdditionalTomcatConnectors(connector);
            log.info("HTTPS 服务已启用: https://localhost:{}", httpsPort);
        };
    }
}

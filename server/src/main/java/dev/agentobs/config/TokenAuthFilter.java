package dev.agentobs.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;

/**
 * 鉴权（SPEC 7.2）：
 * <ul>
 *   <li>/v1/** 校验 X-Agentobs-Token == AGENTOBS_INGEST_TOKEN</li>
 *   <li>/api/internal/** 校验 X-Agentobs-Token == AGENTOBS_INTERNAL_TOKEN</li>
 *   <li>公开接口不做登录；复核人取 X-Forwarded-User</li>
 * </ul>
 * token 未配置（空）时拒绝所有对应请求，避免误开。
 */
@Component
public class TokenAuthFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Agentobs-Token";

    private final AgentobsProperties props;

    public TokenAuthFilter(AgentobsProperties props) {
        this.props = props;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        String path = req.getRequestURI();
        String expected = null;
        if (path.startsWith("/v1/")) {
            expected = props.ingestToken();
        } else if (path.startsWith("/api/internal/")) {
            expected = props.internalToken();
        }
        if (expected != null && !constantTimeEquals(expected, req.getHeader(HEADER))) {
            res.setStatus(401);
            res.setContentType(MediaType.APPLICATION_PROBLEM_JSON_VALUE);
            res.getWriter().write("{\"type\":\"about:blank\",\"title\":\"Unauthorized\",\"status\":401,"
                    + "\"detail\":\"missing or invalid " + HEADER + "\",\"instance\":\"" + path + "\"}");
            return;
        }
        chain.doFilter(req, res);
    }

    static boolean constantTimeEquals(String expected, String actual) {
        if (expected == null || expected.isBlank() || actual == null) return false;
        return MessageDigest.isEqual(
                expected.getBytes(StandardCharsets.UTF_8), actual.getBytes(StandardCharsets.UTF_8));
    }
}

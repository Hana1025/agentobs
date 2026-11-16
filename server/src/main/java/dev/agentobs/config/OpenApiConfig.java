package dev.agentobs.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.info.License;
import org.springdoc.core.models.GroupedOpenApi;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** /v3/api-docs 只包含公开接口与内部接口（入库接口 @Hidden）；前端类型由此生成。 */
@Configuration
public class OpenApiConfig {

    @Bean
    public OpenAPI openAPI() {
        return new OpenAPI().info(new Info()
                .title("agentobs server")
                .version("0.1.0")
                .description("LLM agent observability: query, triage and review API")
                .license(new License().name("Apache-2.0")));
    }

    @Bean
    public GroupedOpenApi all() {
        return GroupedOpenApi.builder().group("default").pathsToMatch("/api/**").build();
    }
}

package dev.agentobs.review;

import dev.agentobs.query.TraceController;
import dev.agentobs.query.dto.PageResponse;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/reviews")
@Tag(name = "reviews")
public class ReviewController {

    private final ReviewService service;

    public ReviewController(ReviewService service) {
        this.service = service;
    }

    @GetMapping("/queue")
    @Operation(operationId = "getReviewQueue", summary = "待复核队列，按 severity、created_at 排序")
    public PageResponse<ReviewService.ReviewQueueItem> queue(
            @RequestParam("project_id") String projectId,
            @RequestParam(value = "page", defaultValue = "0") int page,
            @RequestParam(value = "size", defaultValue = "20") int size) {
        return service.queue(projectId, page, Math.min(Math.max(size, 1), 100));
    }

    @PostMapping
    @Operation(operationId = "submitReview", summary = "提交复核：写 reviews、置 reviewed、生成 eval_case")
    public ReviewService.ReviewResponse submit(@RequestBody @Valid ReviewService.ReviewRequest req, HttpServletRequest http) {
        return service.submit(req, TraceController.currentUser(http));
    }
}

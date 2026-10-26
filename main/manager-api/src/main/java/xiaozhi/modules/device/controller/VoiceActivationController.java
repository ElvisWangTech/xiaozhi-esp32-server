package xiaozhi.modules.device.controller;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import xiaozhi.common.utils.Result;
import xiaozhi.modules.device.dto.VoiceActivationBindDTO;
import xiaozhi.modules.device.dto.VoiceActivationMacDTO;
import xiaozhi.modules.device.dto.VoiceActivationNicknameDTO;
import xiaozhi.modules.device.dto.VoiceActivationSmsDTO;
import xiaozhi.modules.device.service.VoiceActivationService;
import xiaozhi.modules.device.vo.VoiceActivationBindVO;
import xiaozhi.modules.device.vo.VoiceActivationSessionVO;

@Tag(name = "语音激活")
@RestController
@RequestMapping("/device/voice-activation")
public class VoiceActivationController {

    private final VoiceActivationService voiceActivationService;

    public VoiceActivationController(VoiceActivationService voiceActivationService) {
        this.voiceActivationService = voiceActivationService;
    }

    @PostMapping("/session")
    @Operation(summary = "查询语音激活会话")
    public Result<VoiceActivationSessionVO> session(@Valid @RequestBody VoiceActivationMacDTO dto) {
        return new Result<VoiceActivationSessionVO>().ok(voiceActivationService.session(dto.getMacAddress()));
    }

    @PostMapping("/sms")
    @Operation(summary = "向已确认的手机号发送验证码")
    public Result<Void> sendSms(@Valid @RequestBody VoiceActivationSmsDTO dto) {
        voiceActivationService.sendSms(dto);
        return new Result<>();
    }

    @PostMapping("/bind")
    @Operation(summary = "校验验证码并绑定设备")
    public Result<VoiceActivationBindVO> bind(@Valid @RequestBody VoiceActivationBindDTO dto) {
        return new Result<VoiceActivationBindVO>().ok(voiceActivationService.bind(dto));
    }

    @PostMapping("/nickname")
    @Operation(summary = "设置助手昵称")
    public Result<Void> nickname(@Valid @RequestBody VoiceActivationNicknameDTO dto) {
        voiceActivationService.saveNickname(dto);
        return new Result<>();
    }

    @PostMapping("/skills")
    @Operation(summary = "默认技能绑定")
    public Result<Void> skills(@Valid @RequestBody VoiceActivationMacDTO dto) {
        voiceActivationService.bindSkills(dto.getMacAddress());
        return new Result<>();
    }
}

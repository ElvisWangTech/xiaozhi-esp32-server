package xiaozhi.modules.device.controller;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import xiaozhi.common.utils.Result;
import xiaozhi.modules.device.dto.VoiceActivationBindDTO;
import xiaozhi.modules.device.dto.VoiceActivationMacDTO;
import xiaozhi.modules.device.dto.VoiceActivationNicknameDTO;
import xiaozhi.modules.device.dto.VoiceActivationRoleDTO;
import xiaozhi.modules.device.dto.VoiceActivationSkillAuthDTO;
import xiaozhi.modules.device.dto.VoiceActivationSkillCodeDTO;
import xiaozhi.modules.device.dto.VoiceActivationSkillTaskDTO;
import xiaozhi.modules.device.dto.VoiceActivationSmsDTO;
import xiaozhi.modules.device.service.VoiceActivationService;
import xiaozhi.modules.device.vo.VoiceActivationBindVO;
import xiaozhi.modules.device.vo.VoiceActivationSessionVO;
import xiaozhi.modules.device.vo.VoiceActivationSkillAuthVO;
import xiaozhi.modules.device.vo.VoiceActivationTemplateVO;

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

    @PostMapping("/role/templates")
    @Operation(summary = "列出可选的角色性格模板")
    public Result<List<VoiceActivationTemplateVO>> roleTemplates(@Valid @RequestBody VoiceActivationMacDTO dto) {
        return new Result<List<VoiceActivationTemplateVO>>().ok(voiceActivationService.listRoleTemplates(dto.getMacAddress()));
    }

    @PostMapping("/role")
    @Operation(summary = "保存主人称呼和角色性格")
    public Result<Void> role(@Valid @RequestBody VoiceActivationRoleDTO dto) {
        voiceActivationService.saveRole(dto);
        return new Result<>();
    }

    @PostMapping("/skills")
    @Operation(summary = "通用技能绑定并签发邀请令牌")
    public Result<Void> skills(@Valid @RequestBody VoiceActivationMacDTO dto) {
        voiceActivationService.bindSkills(dto.getMacAddress());
        return new Result<>();
    }

    @PostMapping("/skills/auth")
    @Operation(summary = "启动授权技能自动化（滴滴、瑞幸）")
    public Result<VoiceActivationSkillAuthVO> skillsAuth(@Valid @RequestBody VoiceActivationSkillAuthDTO dto) {
        return new Result<VoiceActivationSkillAuthVO>()
                .ok(voiceActivationService.startSkillAuth(dto.getMacAddress(), dto.getService()));
    }

    @PostMapping("/skills/auth/status")
    @Operation(summary = "查询授权技能自动化状态")
    public Result<VoiceActivationSkillAuthVO> skillsAuthStatus(@Valid @RequestBody VoiceActivationSkillTaskDTO dto) {
        return new Result<VoiceActivationSkillAuthVO>()
                .ok(voiceActivationService.skillAuthStatus(dto.getMacAddress(), dto.getTaskId()));
    }

    @PostMapping("/skills/auth/code")
    @Operation(summary = "提交授权技能短信验证码")
    public Result<VoiceActivationSkillAuthVO> skillsAuthCode(@Valid @RequestBody VoiceActivationSkillCodeDTO dto) {
        return new Result<VoiceActivationSkillAuthVO>()
                .ok(voiceActivationService.submitSkillAuthCode(
                        dto.getMacAddress(), dto.getTaskId(), dto.getCode()));
    }

    @PostMapping("/skills/auth/cancel")
    @Operation(summary = "取消授权技能自动化")
    public Result<VoiceActivationSkillAuthVO> skillsAuthCancel(@Valid @RequestBody VoiceActivationSkillTaskDTO dto) {
        return new Result<VoiceActivationSkillAuthVO>()
                .ok(voiceActivationService.cancelSkillAuth(dto.getMacAddress(), dto.getTaskId()));
    }

    @PostMapping("/skills/defer")
    @Operation(summary = "用户跳过技能绑定，之后静默重试")
    public Result<Void> skillsDefer(@Valid @RequestBody VoiceActivationMacDTO dto) {
        voiceActivationService.deferSkills(dto.getMacAddress());
        return new Result<>();
    }

    @PostMapping("/skills/complete")
    @Operation(summary = "完成技能引导")
    public Result<Void> skillsComplete(@Valid @RequestBody VoiceActivationMacDTO dto) {
        voiceActivationService.completeSkills(dto.getMacAddress());
        return new Result<>();
    }
}

package xiaozhi.modules.device.service;

import java.util.Map;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;

import lombok.AllArgsConstructor;
import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.common.redis.RedisKeys;
import xiaozhi.common.redis.RedisUtils;
import xiaozhi.common.utils.ToolUtil;
import xiaozhi.modules.agent.dto.AgentCreateDTO;
import xiaozhi.modules.agent.entity.AgentEntity;
import xiaozhi.modules.agent.service.AgentService;
import xiaozhi.modules.device.dto.VoiceActivationBindDTO;
import xiaozhi.modules.device.dto.VoiceActivationNicknameDTO;
import xiaozhi.modules.device.dto.VoiceActivationSmsDTO;
import xiaozhi.modules.device.entity.DeviceEntity;
import xiaozhi.modules.device.vo.VoiceActivationBindVO;
import xiaozhi.modules.device.vo.VoiceActivationSessionVO;
import xiaozhi.modules.sms.service.DypnsSmsCodeService;
import xiaozhi.modules.sys.dao.SysUserDao;
import xiaozhi.modules.sys.entity.SysUserEntity;
import xiaozhi.modules.sys.enums.SuperAdminEnum;

@Service
@AllArgsConstructor
public class VoiceActivationService {

    public static final String ONBOARDING_NICKNAME = "nickname";
    public static final String ONBOARDING_DONE = "done";
    public static final String ACTIVATION_VOICE = "voice";
    private static final String DEFAULT_AGENT_NAME = "小智";

    private final DeviceService deviceService;
    private final AgentService agentService;
    private final DypnsSmsCodeService smsCodeService;
    private final SysUserDao sysUserDao;
    private final RedisUtils redisUtils;

    public VoiceActivationSessionVO session(String macAddress) {
        String mac = requireMac(macAddress);
        VoiceActivationSessionVO vo = new VoiceActivationSessionVO();
        DeviceEntity device = deviceService.getDeviceByMacAddress(mac);
        if (device != null) {
            vo.setBound(true);
            vo.setOnboardingStatus(device.getOnboardingStatus());
            return vo;
        }
        String code = deviceService.geCodeByDeviceId(mac);
        if (StringUtils.isBlank(code)) {
            throw new RenException(ErrorCode.OTA_DEVICE_NOT_FOUND);
        }
        Map<String, Object> cache = activationData(mac);
        Object type = cache == null ? null : cache.get("activation_type");
        vo.setBound(false);
        vo.setActivationType(type == null ? "code" : String.valueOf(type));
        return vo;
    }

    public void sendSms(VoiceActivationSmsDTO dto) {
        String mac = requireMac(dto.getMacAddress());
        String phone = DypnsSmsCodeService.normalizePhone(dto.getPhone());
        assertPendingActivation(mac);
        Map<String, Object> cache = activationData(mac);
        if (cache == null || !ACTIVATION_VOICE.equals(String.valueOf(cache.get("activation_type")))) {
            throw new RenException(ErrorCode.ACTIVATION_CODE_ERROR);
        }
        smsCodeService.send(phone);
        cache.put("phone", phone);
        redisUtils.set(activationKey(mac), cache);
    }

    @Transactional(rollbackFor = Exception.class)
    public VoiceActivationBindVO bind(VoiceActivationBindDTO dto) {
        String mac = requireMac(dto.getMacAddress());
        String phone = DypnsSmsCodeService.normalizePhone(dto.getPhone());
        if (deviceService.getDeviceByMacAddress(mac) != null) {
            throw new RenException(ErrorCode.DEVICE_ALREADY_ACTIVATED);
        }
        Map<String, Object> cache = activationData(mac);
        if (cache == null || !phone.equals(cache.get("phone"))) {
            throw new RenException("请先确认手机号并获取验证码");
        }
        String activationCode = deviceService.geCodeByDeviceId(mac);
        if (StringUtils.isBlank(activationCode)) {
            throw new RenException(ErrorCode.ACTIVATION_CODE_ERROR);
        }
        smsCodeService.verifyOrThrow(phone, dto.getCode());

        SysUserEntity admin = findSuperAdmin();
        AgentCreateDTO create = new AgentCreateDTO();
        create.setAgentName(DEFAULT_AGENT_NAME);
        String agentId = agentService.createAgentForUser(create, admin.getId());
        deviceService.activateDevice(agentId, activationCode, admin.getId(), phone, ONBOARDING_NICKNAME);

        VoiceActivationBindVO vo = new VoiceActivationBindVO();
        vo.setDeviceId(mac);
        vo.setAgentId(agentId);
        return vo;
    }

    @Transactional(rollbackFor = Exception.class)
    public void saveNickname(VoiceActivationNicknameDTO dto) {
        String mac = requireMac(dto.getMacAddress());
        String nickname = normalizeNickname(dto.getNickname());
        DeviceEntity device = deviceService.getDeviceByMacAddress(mac);
        if (device == null) {
            throw new RenException(ErrorCode.DEVICE_NOT_EXIST);
        }
        if (!ONBOARDING_NICKNAME.equals(device.getOnboardingStatus())) {
            throw new RenException("当前不需要设置助手昵称");
        }
        AgentEntity agent = agentService.selectById(device.getAgentId());
        if (agent == null) {
            throw new RenException(ErrorCode.AGENT_NOT_FOUND);
        }
        agent.setAgentName(nickname);
        agentService.updateById(agent);
        device.setOnboardingStatus(ONBOARDING_DONE);
        deviceService.updateById(device);
    }

    private SysUserEntity findSuperAdmin() {
        QueryWrapper<SysUserEntity> wrapper = new QueryWrapper<>();
        wrapper.eq("super_admin", SuperAdminEnum.YES.value());
        wrapper.orderByAsc("id");
        wrapper.last("LIMIT 1");
        SysUserEntity admin = sysUserDao.selectOne(wrapper);
        if (admin == null || admin.getId() == null) {
            throw new RenException("没有可用的管理员账号");
        }
        return admin;
    }

    private void assertPendingActivation(String mac) {
        if (deviceService.getDeviceByMacAddress(mac) != null) {
            throw new RenException(ErrorCode.DEVICE_ALREADY_ACTIVATED);
        }
        if (StringUtils.isBlank(deviceService.geCodeByDeviceId(mac))) {
            throw new RenException(ErrorCode.ACTIVATION_CODE_ERROR);
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> activationData(String mac) {
        Object value = redisUtils.get(activationKey(mac));
        if (ToolUtil.isEmpty(value) || !(value instanceof Map)) {
            return null;
        }
        return (Map<String, Object>) value;
    }

    private static String activationKey(String mac) {
        return RedisKeys.getOtaDeviceActivationInfo(mac.replace(":", "_").toLowerCase());
    }

    private static String requireMac(String macAddress) {
        if (StringUtils.isBlank(macAddress)) {
            throw new RenException(ErrorCode.MCA_NOT_NULL);
        }
        return macAddress.trim();
    }

    private static String normalizeNickname(String nickname) {
        if (nickname == null) {
            throw new RenException("请说出助手昵称");
        }
        String trimmed = nickname.trim().replaceAll("[\\s，。,.!！?？]", "");
        if (trimmed.isEmpty() || trimmed.length() > 12) {
            throw new RenException("昵称请控制在1到12个字");
        }
        return trimmed;
    }
}

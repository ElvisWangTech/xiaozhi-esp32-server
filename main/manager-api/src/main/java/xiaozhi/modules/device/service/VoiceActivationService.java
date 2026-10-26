package xiaozhi.modules.device.service;

import java.util.HashMap;
import java.util.Map;

import org.apache.commons.lang3.StringUtils;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.client.RestTemplate;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.common.redis.RedisKeys;
import xiaozhi.common.redis.RedisUtils;
import xiaozhi.common.utils.ToolUtil;
import xiaozhi.modules.agent.dto.AgentCreateDTO;
import xiaozhi.modules.agent.entity.AgentEntity;
import xiaozhi.modules.agent.service.AgentMcpAccessPointService;
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
import xiaozhi.modules.sys.service.SysParamsService;

@Slf4j
@Service
@AllArgsConstructor
public class VoiceActivationService {

    public static final String ONBOARDING_NICKNAME = "nickname";
    public static final String ONBOARDING_SKILLS = "skills";
    public static final String ONBOARDING_DONE = "done";
    public static final String ACTIVATION_VOICE = "voice";
    private static final String DEFAULT_AGENT_NAME = "小智";
    private static final String PARAM_PORTAL_BASE_URL = "portal_lite.base_url";
    private static final String PARAM_PORTAL_BIND_TOKEN = "portal_lite.bind_token";

    private final DeviceService deviceService;
    private final AgentService agentService;
    private final AgentMcpAccessPointService agentMcpAccessPointService;
    private final DypnsSmsCodeService smsCodeService;
    private final SysUserDao sysUserDao;
    private final SysParamsService sysParamsService;
    private final RedisUtils redisUtils;
    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

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
        device.setOnboardingStatus(ONBOARDING_SKILLS);
        deviceService.updateById(device);
    }

    @Transactional(rollbackFor = Exception.class)
    public void bindSkills(String macAddress) {
        String mac = requireMac(macAddress);
        DeviceEntity device = deviceService.getDeviceByMacAddress(mac);
        if (device == null) {
            throw new RenException(ErrorCode.DEVICE_NOT_EXIST);
        }
        if (!ONBOARDING_SKILLS.equals(device.getOnboardingStatus())) {
            throw new RenException("当前不需要绑定技能");
        }
        if (StringUtils.isBlank(device.getPhone())) {
            throw new RenException("设备未记录手机号，无法绑定技能");
        }
        AgentEntity agent = agentService.selectById(device.getAgentId());
        if (agent == null) {
            throw new RenException(ErrorCode.AGENT_NOT_FOUND);
        }
        String endpointUrl = agentMcpAccessPointService.getAgentMcpAccessAddress(agent.getId());
        if (StringUtils.isBlank(endpointUrl) || !endpointUrl.startsWith("ws")) {
            throw new RenException("智能体 MCP 接入点未配置");
        }
        String baseUrl = StringUtils.trimToEmpty(sysParamsService.getValue(PARAM_PORTAL_BASE_URL, true));
        String bindToken = StringUtils.trimToEmpty(sysParamsService.getValue(PARAM_PORTAL_BIND_TOKEN, true));
        if (StringUtils.isBlank(baseUrl) || StringUtils.isBlank(bindToken)) {
            throw new RenException("技能服务暂不可用");
        }
        String agentName = StringUtils.defaultIfBlank(agent.getAgentName(), DEFAULT_AGENT_NAME);
        String deviceName = agentName;
        Map<String, Object> body = new HashMap<>();
        body.put("device_name", deviceName);
        body.put("mac_address", mac);
        body.put("agent_name", agentName);
        body.put("endpoint_url", endpointUrl);
        body.put("phone", device.getPhone());
        callPortalBind(baseUrl, bindToken, body);
        device.setOnboardingStatus(ONBOARDING_DONE);
        deviceService.updateById(device);
    }

    private void callPortalBind(String baseUrl, String bindToken, Map<String, Object> body) {
        String url = StringUtils.removeEnd(baseUrl, "/") + "/api/device/bind";
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.set("X-Bind-Token", bindToken);
        try {
            ResponseEntity<String> response = restTemplate.exchange(
                    url,
                    HttpMethod.POST,
                    new HttpEntity<>(body, headers),
                    String.class);
            String payload = response.getBody();
            if (!response.getStatusCode().is2xxSuccessful()) {
                log.warn("portal_lite bind failed: url={} status={} body={}",
                        url, response.getStatusCode().value(), abbreviate(payload));
                throw new RenException(portalErrorMessage(payload, response.getStatusCode().value()));
            }
            JsonNode root = StringUtils.isBlank(payload) ? null : objectMapper.readTree(payload);
            if (root != null && root.has("ok") && !root.path("ok").asBoolean(false)) {
                throw new RenException(portalErrorMessage(payload, response.getStatusCode().value()));
            }
        } catch (RenException e) {
            throw e;
        } catch (RestClientResponseException e) {
            log.warn("portal_lite bind failed: url={} status={} body={}",
                    url, e.getStatusCode().value(), abbreviate(e.getResponseBodyAsString()));
            throw new RenException(portalErrorMessage(e.getResponseBodyAsString(), e.getStatusCode().value()));
        } catch (Exception e) {
            log.warn("portal_lite bind error: url={} err={}", url, e.getMessage());
            throw new RenException("技能服务连不上，请稍后再试");
        }
    }

    private String portalErrorMessage(String payload, int status) {
        if (StringUtils.isNotBlank(payload)) {
            String trimmed = payload.trim();
            if (trimmed.startsWith("<") || trimmed.toLowerCase().contains("<!doctype")) {
                if (status == 404) {
                    return "技能服务接口不存在，请升级 portal_lite";
                }
                return "技能服务返回异常页面";
            }
            try {
                JsonNode root = objectMapper.readTree(payload);
                String error = root.path("error").asText(null);
                if (StringUtils.isNotBlank(error) && error.length() <= 40) {
                    return error;
                }
            } catch (Exception ignored) {
                // keep status-based fallback
            }
        }
        if (status == 401 || status == 403) {
            return "技能服务口令错误";
        }
        if (status == 404) {
            return "技能服务接口不存在，请升级 portal_lite";
        }
        return "技能绑定失败";
    }

    private static String abbreviate(String text) {
        if (text == null) {
            return "";
        }
        String oneLine = text.replace('\n', ' ').replace('\r', ' ').trim();
        return oneLine.length() <= 200 ? oneLine : oneLine.substring(0, 200);
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

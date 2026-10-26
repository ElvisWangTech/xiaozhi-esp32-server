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
import org.springframework.web.util.UriComponentsBuilder;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import xiaozhi.common.exception.ErrorCode;
import xiaozhi.common.exception.RenException;
import xiaozhi.common.redis.RedisKeys;
import xiaozhi.common.redis.RedisUtils;
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
import xiaozhi.modules.device.vo.VoiceActivationSkillAuthVO;
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
    private static final String PARAM_PORTAL_ADMIN_TOKEN = "portal_lite.admin_token";
    private static final long PORTAL_INVITE_TTL_SECONDS = 1800L;

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
        DeviceEntity device = requireSkillsDevice(mac);
        AgentEntity agent = requireAgent(device);
        String endpointUrl = agentMcpAccessPointService.getAgentMcpAccessAddress(agent.getId());
        if (StringUtils.isBlank(endpointUrl) || !endpointUrl.startsWith("ws")) {
            throw new RenException("智能体 MCP 接入点未配置");
        }
        PortalConfig portal = requirePortalConfig();
        String agentName = StringUtils.defaultIfBlank(agent.getAgentName(), DEFAULT_AGENT_NAME);
        Map<String, Object> body = new HashMap<>();
        body.put("device_name", agentName);
        body.put("mac_address", mac);
        body.put("agent_name", agentName);
        body.put("endpoint_url", endpointUrl);
        body.put("phone", device.getPhone());
        JsonNode bindResult = callPortalJson(portal.baseUrl, portal.bindToken, null,
                HttpMethod.POST, "/api/device/bind", body);
        String portalDeviceId = textOrNull(bindResult, "device_id");
        if (StringUtils.isBlank(portalDeviceId)) {
            throw new RenException("技能服务未返回设备标识");
        }
        cachePortalInvite(mac, portalDeviceId, issueInviteToken(portal, portalDeviceId));
    }

    public VoiceActivationSkillAuthVO startSkillAuth(String macAddress, String service) {
        String mac = requireMac(macAddress);
        DeviceEntity device = requireSkillsDevice(mac);
        String normalized = normalizeAuthService(service);
        PortalInvite invite = requirePortalInvite(mac);
        PortalConfig portal = requirePortalConfig();
        Map<String, Object> body = new HashMap<>();
        body.put("token", invite.token);
        body.put("service", normalized);
        body.put("phone", device.getPhone());
        JsonNode started = callPortalJson(portal.baseUrl, null, null,
                HttpMethod.POST, "/api/automation/start", body);
        return toAuthVO(normalized, started);
    }

    public VoiceActivationSkillAuthVO skillAuthStatus(String macAddress, String taskId) {
        requireMac(macAddress);
        requireSkillsDevice(macAddress);
        String tid = requireTaskId(taskId);
        PortalConfig portal = requirePortalConfig();
        String url = UriComponentsBuilder
                .fromUriString(StringUtils.removeEnd(portal.baseUrl, "/") + "/api/automation/status")
                .queryParam("task_id", tid)
                .toUriString();
        JsonNode view = callPortalJsonUrl(null, null, HttpMethod.GET, url, null);
        String service = textOrNull(view, "service");
        return toAuthVO(StringUtils.defaultString(service), view);
    }

    public VoiceActivationSkillAuthVO submitSkillAuthCode(String macAddress, String taskId, String code) {
        String mac = requireMac(macAddress);
        requireSkillsDevice(mac);
        String tid = requireTaskId(taskId);
        String smsCode = StringUtils.trimToEmpty(code);
        if (smsCode.isEmpty()) {
            throw new RenException("请说出验证码");
        }
        PortalConfig portal = requirePortalConfig();
        Map<String, Object> body = new HashMap<>();
        body.put("task_id", tid);
        body.put("code", smsCode);
        callPortalJson(portal.baseUrl, null, null, HttpMethod.POST, "/api/automation/code", body);
        VoiceActivationSkillAuthVO vo = skillAuthStatus(mac, tid);
        if ("pending".equals(vo.getStatus()) || "waiting_code".equals(vo.getStatus())) {
            vo.setStatus("pending");
            if (StringUtils.isBlank(vo.getMessage())) {
                vo.setMessage("验证码已提交，正在登录");
            }
        }
        return vo;
    }

    public VoiceActivationSkillAuthVO cancelSkillAuth(String macAddress, String taskId) {
        String mac = requireMac(macAddress);
        requireSkillsDevice(mac);
        String tid = requireTaskId(taskId);
        PortalConfig portal = requirePortalConfig();
        Map<String, Object> body = new HashMap<>();
        body.put("task_id", tid);
        try {
            callPortalJson(portal.baseUrl, null, null, HttpMethod.POST, "/api/automation/cancel", body);
        } catch (RenException e) {
            // 任务已结束也视为跳过
            log.info("cancel skill auth task={} msg={}", tid, e.getMessage());
        }
        VoiceActivationSkillAuthVO vo = new VoiceActivationSkillAuthVO();
        vo.setStatus("skipped");
        vo.setTaskId(tid);
        vo.setMessage("已取消");
        return vo;
    }

    @Transactional(rollbackFor = Exception.class)
    public void completeSkills(String macAddress) {
        String mac = requireMac(macAddress);
        DeviceEntity device = requireSkillsDevice(mac);
        finishPortalInvite(mac);
        device.setOnboardingStatus(ONBOARDING_DONE);
        deviceService.updateById(device);
        redisUtils.delete(RedisKeys.getVoicePortalInviteKey(mac));
    }

    private void finishPortalInvite(String mac) {
        PortalInvite invite = loadPortalInvite(mac);
        if (invite == null || StringUtils.isBlank(invite.token)) {
            return;
        }
        try {
            PortalConfig portal = requirePortalConfig();
            Map<String, Object> body = new HashMap<>();
            body.put("token", invite.token);
            callPortalJson(portal.baseUrl, null, null, HttpMethod.POST, "/api/portal/finish", body);
        } catch (Exception e) {
            log.info("portal finish invite skipped: mac={} err={}", mac, e.getMessage());
        }
    }

    private DeviceEntity requireSkillsDevice(String mac) {
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
        return device;
    }

    private AgentEntity requireAgent(DeviceEntity device) {
        AgentEntity agent = agentService.selectById(device.getAgentId());
        if (agent == null) {
            throw new RenException(ErrorCode.AGENT_NOT_FOUND);
        }
        return agent;
    }

    private PortalConfig requirePortalConfig() {
        String baseUrl = StringUtils.trimToEmpty(sysParamsService.getValue(PARAM_PORTAL_BASE_URL, true));
        String bindToken = StringUtils.trimToEmpty(sysParamsService.getValue(PARAM_PORTAL_BIND_TOKEN, true));
        String adminToken = StringUtils.trimToEmpty(sysParamsService.getValue(PARAM_PORTAL_ADMIN_TOKEN, true));
        if (StringUtils.isBlank(adminToken)) {
            adminToken = bindToken;
        }
        if (StringUtils.isBlank(baseUrl) || StringUtils.isBlank(bindToken)) {
            throw new RenException("技能服务暂不可用");
        }
        return new PortalConfig(baseUrl, bindToken, adminToken);
    }

    private String issueInviteToken(PortalConfig portal, String portalDeviceId) {
        if (StringUtils.isBlank(portal.adminToken)) {
            throw new RenException("技能服务管理员口令未配置");
        }
        Map<String, Object> body = new HashMap<>();
        body.put("device_id", portalDeviceId);
        JsonNode invite = callPortalJson(portal.baseUrl, null, portal.adminToken,
                HttpMethod.POST, "/api/portal/invite", body);
        String token = textOrNull(invite, "token");
        if (StringUtils.isBlank(token)) {
            throw new RenException("技能服务未返回邀请令牌");
        }
        return token;
    }

    private void cachePortalInvite(String mac, String portalDeviceId, String token) {
        Map<String, Object> cache = new HashMap<>();
        cache.put("device_id", portalDeviceId);
        cache.put("token", token);
        redisUtils.set(RedisKeys.getVoicePortalInviteKey(mac), cache, PORTAL_INVITE_TTL_SECONDS);
    }

    private PortalInvite requirePortalInvite(String mac) {
        PortalInvite invite = loadPortalInvite(mac);
        if (invite == null || StringUtils.isBlank(invite.token) || StringUtils.isBlank(invite.deviceId)) {
            throw new RenException("请先完成通用技能绑定");
        }
        return invite;
    }

    @SuppressWarnings("unchecked")
    private PortalInvite loadPortalInvite(String mac) {
        Object value = redisUtils.get(RedisKeys.getVoicePortalInviteKey(mac));
        if (!(value instanceof Map<?, ?> mapValue) || mapValue.isEmpty()) {
            return null;
        }
        Map<String, Object> map = (Map<String, Object>) value;
        Object deviceId = map.get("device_id");
        Object token = map.get("token");
        if (deviceId == null || token == null) {
            return null;
        }
        return new PortalInvite(String.valueOf(deviceId), String.valueOf(token));
    }

    private static String normalizeAuthService(String service) {
        if (service == null) {
            throw new RenException("请指定要授权的技能");
        }
        String raw = service.trim().toLowerCase();
        if ("didi".equals(raw) || "滴滴".equals(service.trim()) || "滴滴打车".equals(service.trim())
                || "滴滴出行".equals(service.trim())) {
            return "didi";
        }
        if ("luckin".equals(raw) || "瑞幸".equals(service.trim()) || "瑞幸咖啡".equals(service.trim())) {
            return "luckin";
        }
        if ("mcd".equals(raw) || "麦当劳".equals(service.trim())) {
            throw new RenException("麦当劳授权绑定暂不支持");
        }
        throw new RenException("暂不支持该技能授权");
    }

    private VoiceActivationSkillAuthVO toAuthVO(String service, JsonNode view) {
        VoiceActivationSkillAuthVO vo = new VoiceActivationSkillAuthVO();
        vo.setService(service);
        if (view == null) {
            vo.setStatus("failed");
            vo.setMessage("任务不存在");
            return vo;
        }
        String taskId = textOrNull(view, "task_id");
        String state = textOrNull(view, "state");
        String message = textOrNull(view, "message");
        vo.setTaskId(taskId);
        vo.setMessage(message);
        if (view.path("done").asBoolean(false) || "done".equals(state)) {
            vo.setStatus("done");
        } else if (view.path("canceled").asBoolean(false) || "canceled".equals(state)) {
            vo.setStatus("skipped");
        } else if (view.path("error").asBoolean(false) || "error".equals(state)) {
            vo.setStatus("failed");
            if (StringUtils.isBlank(vo.getMessage())) {
                vo.setMessage("授权失败");
            }
        } else if ("waiting_code".equals(state)) {
            vo.setStatus("waiting_code");
            if (StringUtils.isBlank(vo.getMessage())) {
                vo.setMessage("请输入短信验证码");
            }
        } else if ("waiting_scan".equals(state)) {
            vo.setStatus("failed");
            vo.setMessage("该技能需要扫码，语音暂不支持");
        } else {
            vo.setStatus("pending");
        }
        return vo;
    }

    private JsonNode callPortalJson(String baseUrl, String bindToken, String adminToken,
            HttpMethod method, String path, Map<String, Object> body) {
        String url = StringUtils.removeEnd(baseUrl, "/") + path;
        return callPortalJsonUrl(bindToken, adminToken, method, url, body);
    }

    private JsonNode callPortalJsonUrl(String bindToken, String adminToken,
            HttpMethod method, String url, Map<String, Object> body) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        if (StringUtils.isNotBlank(bindToken)) {
            headers.set("X-Bind-Token", bindToken);
        }
        if (StringUtils.isNotBlank(adminToken)) {
            headers.set("X-Admin-Token", adminToken);
        }
        try {
            ResponseEntity<String> response = restTemplate.exchange(
                    url,
                    method,
                    new HttpEntity<>(body, headers),
                    String.class);
            String payload = response.getBody();
            if (!response.getStatusCode().is2xxSuccessful()) {
                log.warn("portal_lite call failed: url={} status={} body={}",
                        url, response.getStatusCode().value(), abbreviate(payload));
                throw new RenException(portalErrorMessage(payload, response.getStatusCode().value()));
            }
            if (StringUtils.isBlank(payload)) {
                return objectMapper.createObjectNode();
            }
            JsonNode root = objectMapper.readTree(payload);
            if (root.has("ok") && !root.path("ok").asBoolean(true)) {
                throw new RenException(portalErrorMessage(payload, response.getStatusCode().value()));
            }
            // 纯错误体（无 task_id/state）；自动化任务体即使有 message 也不当失败
            if (root.has("error")
                    && StringUtils.isNotBlank(root.path("error").asText())
                    && !root.has("task_id")
                    && !root.has("state")) {
                throw new RenException(portalErrorMessage(payload, response.getStatusCode().value()));
            }
            return root;
        } catch (RenException e) {
            throw e;
        } catch (RestClientResponseException e) {
            log.warn("portal_lite call failed: url={} status={} body={}",
                    url, e.getStatusCode().value(), abbreviate(e.getResponseBodyAsString()));
            throw new RenException(portalErrorMessage(e.getResponseBodyAsString(), e.getStatusCode().value()));
        } catch (Exception e) {
            log.warn("portal_lite call error: url={} err={}", url, e.getMessage());
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
        if (status == 429) {
            return "授权太频繁，请稍后再试";
        }
        return "技能绑定失败";
    }

    private static String textOrNull(JsonNode node, String field) {
        if (node == null || !node.has(field) || node.get(field).isNull()) {
            return null;
        }
        String text = node.get(field).asText(null);
        return StringUtils.isBlank(text) ? null : text;
    }

    private static String requireTaskId(String taskId) {
        if (StringUtils.isBlank(taskId)) {
            throw new RenException("任务ID不能为空");
        }
        return taskId.trim();
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
        if (!(value instanceof Map<?, ?> map) || map.isEmpty()) {
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

    private static final class PortalConfig {
        private final String baseUrl;
        private final String bindToken;
        private final String adminToken;

        private PortalConfig(String baseUrl, String bindToken, String adminToken) {
            this.baseUrl = baseUrl;
            this.bindToken = bindToken;
            this.adminToken = adminToken;
        }
    }

    private static final class PortalInvite {
        private final String deviceId;
        private final String token;

        private PortalInvite(String deviceId, String token) {
            this.deviceId = deviceId;
            this.token = token;
        }
    }
}

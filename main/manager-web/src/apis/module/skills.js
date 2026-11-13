import { getServiceUrl } from '../api';
import RequestService from '../httpRequest';

function request(method, path, data, callback) {
    RequestService.sendRequest()
        .url(`${getServiceUrl()}${path}`)
        .method(method)
        .data(data || {})
        .success((res) => {
            RequestService.clearRequestTime();
            callback && callback(res);
        })
        .networkFail((error) => {
            RequestService.clearRequestTime();
            console.error('Skills request failed:', error);
            callback && callback({ data: { code: -1, msg: 'Network request failed' } });
        })
        .fail((error) => {
            RequestService.clearRequestTime();
            callback && callback(error);
        }).send();
}

export default {
    getMarketplace(agentId, callback) {
        request('GET', `/skills/marketplace?agentId=${encodeURIComponent(agentId)}`, null, callback);
    },
    bindSkill(agentId, skillId, callback) {
        request('POST', `/skills/${encodeURIComponent(skillId)}/bind?agentId=${encodeURIComponent(agentId)}`, {}, callback);
    },
    unbindSkill(agentId, skillId, callback) {
        request('DELETE', `/skills/${encodeURIComponent(skillId)}/bind?agentId=${encodeURIComponent(agentId)}`, null, callback);
    },
    getAdminSkills(callback) {
        request('GET', '/skills/admin/list', null, callback);
    },
    getAgentsForUser(userId, callback) {
        request('GET', `/skills/admin/users/${encodeURIComponent(userId)}/agents`, null, callback);
    },
    getRecommendedSkillPackages(callback) {
        request('GET', '/skills/admin/recommended', null, callback);
    },
    installRecommendedSkillPackage(recommendationKey, callback) {
        request('POST', `/skills/admin/recommended/${encodeURIComponent(recommendationKey)}/install`, {}, callback);
    },
    createSkill(data, callback) {
        request('POST', '/skills/admin', data, callback);
    },
    updateSkill(skillId, data, callback) {
        request('PUT', `/skills/admin/${encodeURIComponent(skillId)}`, data, callback);
    },
    disableSkill(skillId, callback) {
        request('DELETE', `/skills/admin/${encodeURIComponent(skillId)}`, null, callback);
    }
};

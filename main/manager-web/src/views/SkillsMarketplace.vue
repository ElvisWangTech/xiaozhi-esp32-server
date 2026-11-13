<template>
  <div class="welcome">
    <HeaderBar />
    <div class="main-wrapper">
      <div class="content-panel">
        <div class="content-area">
          <el-card shadow="never" class="skills-page-card">
            <div class="skills-header">
              <div>
                <h2 class="page-title">{{ $t('skills.title') }}</h2>
                <p class="skills-subtitle">{{ $t('skills.subtitle') }}</p>
              </div>
              <div class="skills-agent-select">
                <template v-if="isSuperAdmin">
                  <span>{{ $t('skills.targetUser') }}</span>
                  <el-select
                    v-model="selectedTargetUserId"
                    filterable
                    remote
                    clearable
                    :remote-method="searchUsers"
                    :loading="userSearchLoading"
                    :placeholder="$t('skills.selectTargetUser')"
                    @change="handleTargetUserChange"
                  >
                    <el-option
                      v-for="user in targetUsers"
                      :key="user.userid"
                      :label="user.mobile || user.userid"
                      :value="user.userid"
                    />
                  </el-select>
                  <span>{{ $t('skills.targetAgent') }}</span>
                </template>
                <span v-else>{{ $t('skills.agent') }}</span>
                <el-select
                  v-if="isSuperAdmin || agents.length"
                  v-model="selectedAgentId"
                  :disabled="isSuperAdmin && (!selectedTargetUserId || targetAgentsLoading)"
                  :loading="targetAgentsLoading"
                  :placeholder="$t('skills.selectAgent')"
                  @change="loadMarketplace"
                >
                  <el-option
                    v-for="agent in agentOptions"
                    :key="agent.id"
                    :label="agent.agentName"
                    :value="agent.id"
                  />
                </el-select>
                <el-alert v-if="!isSuperAdmin && !agents.length" :title="$t('skills.noAgents')" type="info" :closable="false" />
              </div>
            </div>

            <el-alert
              :title="$t('skills.runtimeNotice')"
              type="warning"
              :closable="false"
              show-icon
              class="skills-runtime-notice"
            />

            <div v-loading="marketplaceLoading" class="skills-marketplace">
              <div v-if="marketplaceSkills.length" class="skills-grid">
                <el-card v-for="skill in marketplaceSkills" :key="skill.id" shadow="hover" class="skill-card">
                  <div class="skill-card-top">
                    <div>
                      <h3>{{ skill.name }}</h3>
                      <span class="skill-code">{{ skill.skillCode }}</span>
                    </div>
                    <el-tag size="mini" :type="skill.skillType === 'mcp' ? 'primary' : 'success'">
                      {{ skill.skillType === 'mcp' ? $t('skills.typeMcp') : $t('skills.typePackage') }}
                    </el-tag>
                    <el-tag size="mini" type="info">{{ skill.category || $t('skills.uncategorized') }}</el-tag>
                  </div>
                  <p class="skill-description">{{ skill.description || $t('skills.noDescription') }}</p>
                  <div v-if="skill.skillType === 'skill_package' && (skill.sourceUrl || skill.license)" class="skill-provenance">
                    <a v-if="skill.sourceUrl" :href="skill.sourceUrl" target="_blank" rel="noopener noreferrer">
                      {{ $t('skills.sourceLink') }}
                    </a>
                    <span v-if="skill.license">{{ $t('skills.license') }}: {{ skill.license }}</span>
                  </div>
                  <div class="skill-card-footer">
                    <el-tag v-if="skill.bound" size="mini" type="success">{{ $t('skills.enabled') }}</el-tag>
                    <span v-else class="skill-unbound">{{ $t('skills.notEnabled') }}</span>
                    <el-button
                      size="mini"
                      :type="skill.bound ? 'default' : 'primary'"
                      :loading="busySkillId === skill.id"
                      :disabled="!selectedAgentId"
                      @click="toggleBinding(skill)"
                    >
                      {{ skill.bound ? $t('skills.unbind') : $t('skills.enable') }}
                    </el-button>
                  </div>
                </el-card>
              </div>
              <el-empty
                v-else-if="!marketplaceLoading"
                :description="isSuperAdmin && !selectedAgentId ? $t('skills.selectUserAndAgent') : $t('skills.noSkills')"
              />
            </div>
          </el-card>

          <el-card v-if="isSuperAdmin" shadow="never" class="skills-admin-card">
            <div class="skills-admin-header">
              <h2>{{ $t('skills.adminTitle') }}</h2>
              <el-button type="primary" size="small" icon="el-icon-plus" @click="openCreateDialog">
                {{ $t('skills.create') }}
              </el-button>
            </div>
            <div class="recommended-section">
              <div class="recommended-heading">
                <div>
                  <h3>{{ $t('skills.recommendedTitle') }}</h3>
                  <p>{{ $t('skills.recommendedIntro') }}</p>
                </div>
                <el-button size="mini" :loading="recommendedLoading" @click="loadRecommendedSkills">
                  {{ $t('skills.refreshRecommendations') }}
                </el-button>
              </div>
              <div v-loading="recommendedLoading" class="recommended-grid">
                <el-card v-for="item in recommendedSkills" :key="item.key" shadow="never" class="recommended-card">
                  <div class="recommended-card-heading">
                    <strong>{{ item.name }}</strong>
                    <el-tag size="mini" type="info">{{ item.license }}</el-tag>
                  </div>
                  <p>{{ item.description }}</p>
                  <div class="recommended-card-footer">
                    <a :href="item.sourceUrl" target="_blank" rel="noopener noreferrer">{{ $t('skills.sourceLink') }}</a>
                    <el-tag v-if="item.installed" size="mini" type="success">{{ $t('skills.installed') }}</el-tag>
                    <el-button
                      v-else
                      type="primary"
                      size="mini"
                      :loading="busyRecommendationKey === item.key"
                      @click="installRecommendedSkill(item)"
                    >{{ $t('skills.installRecommended') }}</el-button>
                  </div>
                </el-card>
              </div>
            </div>
            <el-table :data="adminSkills" v-loading="adminLoading" stripe>
              <el-table-column prop="name" :label="$t('skills.name')" min-width="140" />
              <el-table-column prop="skillCode" :label="$t('skills.skillCode')" min-width="120" />
              <el-table-column :label="$t('skills.type')" width="130">
                <template slot-scope="scope">
                  {{ scope.row.skillType === 'mcp' ? $t('skills.typeMcp') : $t('skills.typePackage') }}
                </template>
              </el-table-column>
              <el-table-column prop="category" :label="$t('skills.category')" min-width="100" />
              <el-table-column :label="$t('skills.endpointOrSource')" min-width="220">
                <template slot-scope="scope">
                  <span
                    class="endpoint-preview"
                    :title="scope.row.skillType === 'mcp' ? scope.row.endpointUrl : scope.row.sourceUrl"
                  >{{ scope.row.skillType === 'mcp' ? scope.row.endpointUrl : (scope.row.sourceUrl || scope.row.license || '—') }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('skills.status')" width="110">
                <template slot-scope="scope">
                  <el-tag size="mini" :type="scope.row.status === 1 ? 'success' : 'info'">
                    {{ scope.row.status === 1 ? $t('skills.published') : $t('skills.draft') }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('skills.actions')" width="150" fixed="right">
                <template slot-scope="scope">
                  <el-button type="text" size="mini" @click="openEditDialog(scope.row)">{{ $t('skills.edit') }}</el-button>
                  <el-button v-if="scope.row.status === 1" type="text" size="mini" @click="disableSkill(scope.row)">{{ $t('skills.takeDown') }}</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </div>
      </div>
    </div>

    <el-dialog :title="dialogTitle" :visible.sync="dialogVisible" width="720px" :close-on-click-modal="false">
      <el-form ref="skillForm" :model="form" :rules="formRules" label-width="130px">
        <el-form-item :label="$t('skills.skillCode')" prop="skillCode">
          <el-input v-model="form.skillCode" :disabled="!!form.id" maxlength="32" />
        </el-form-item>
        <el-form-item :label="$t('skills.type')" prop="skillType">
          <el-select v-model="form.skillType" :disabled="!!form.id" class="full-width">
            <el-option value="mcp" :label="$t('skills.typeMcp')" />
            <el-option value="skill_package" :label="$t('skills.typePackage')" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('skills.name')" prop="name">
          <el-input v-model="form.name" maxlength="128" />
        </el-form-item>
        <el-form-item :label="$t('skills.category')" prop="category">
          <el-input v-model="form.category" maxlength="64" />
        </el-form-item>
        <el-form-item :label="$t('skills.description')" prop="description">
          <el-input v-model="form.description" type="textarea" :rows="3" maxlength="1000" show-word-limit />
        </el-form-item>
        <el-form-item v-if="form.skillType === 'mcp'" :label="$t('skills.endpoint')" prop="endpointUrl">
          <el-input v-model="form.endpointUrl" type="textarea" :rows="2" placeholder="wss://example.com/mcp/?token=..." />
        </el-form-item>
        <template v-else>
          <el-form-item :label="$t('skills.skillContent')" prop="skillContent">
            <el-input
              v-model="form.skillContent"
              type="textarea"
              :rows="12"
              maxlength="32768"
              show-word-limit
              :placeholder="$t('skills.skillContentPlaceholder')"
            />
            <div class="form-tip">{{ $t('skills.skillContentTip') }}</div>
          </el-form-item>
          <el-form-item :label="$t('skills.referenceContent')" prop="referenceContent">
            <el-input
              v-model="form.referenceContent"
              type="textarea"
              :rows="6"
              maxlength="32768"
              show-word-limit
              :placeholder="$t('skills.referenceContentPlaceholder')"
            />
          </el-form-item>
          <el-form-item :label="$t('skills.sourceUrl')" prop="sourceUrl">
            <el-input v-model="form.sourceUrl" maxlength="2048" />
          </el-form-item>
          <el-form-item :label="$t('skills.license')" prop="license">
            <el-input v-model="form.license" maxlength="64" />
          </el-form-item>
        </template>
        <el-form-item :label="$t('skills.status')">
          <el-switch v-model="form.status" :active-value="1" :inactive-value="0" />
          <span class="switch-label">{{ form.status === 1 ? $t('skills.published') : $t('skills.draft') }}</span>
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button @click="dialogVisible = false">{{ $t('skills.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="saveSkill">{{ $t('skills.save') }}</el-button>
      </span>
    </el-dialog>

    <el-footer><VersionFooter /></el-footer>
  </div>
</template>

<script>
import Api from '@/apis/api';
import HeaderBar from '@/components/HeaderBar.vue';
import VersionFooter from '@/components/VersionFooter.vue';
import { mapState } from 'vuex';

const emptyForm = () => ({
  id: '',
  skillCode: '',
  name: '',
  description: '',
  category: '',
  skillType: 'mcp',
  endpointUrl: '',
  skillContent: '',
  referenceContent: '',
  sourceUrl: '',
  license: '',
  status: 0
});

export default {
  name: 'SkillsMarketplace',
  components: { HeaderBar, VersionFooter },
  data() {
    return {
      agents: [],
      targetUsers: [],
      targetAgents: [],
      selectedAgentId: '',
      selectedTargetUserId: '',
      marketplaceSkills: [],
      adminSkills: [],
      recommendedSkills: [],
      marketplaceLoading: false,
      adminLoading: false,
      recommendedLoading: false,
      userSearchLoading: false,
      targetAgentsLoading: false,
      busySkillId: '',
      busyRecommendationKey: '',
      dialogVisible: false,
      dialogTitle: '',
      saving: false,
      form: emptyForm(),
      formRules: {
        skillCode: [{ required: true, message: this.$t('skills.requiredCode'), trigger: 'blur' }],
        name: [{ required: true, message: this.$t('skills.requiredName'), trigger: 'blur' }],
        endpointUrl: [{
          validator: (rule, value, callback) => {
            if (this.form.skillType === 'mcp' && !String(value || '').trim()) {
              callback(new Error(this.$t('skills.requiredEndpoint')));
            } else callback();
          },
          trigger: 'blur'
        }],
        skillContent: [{
          validator: (rule, value, callback) => {
            if (this.form.skillType === 'skill_package' && !String(value || '').trim()) {
              callback(new Error(this.$t('skills.requiredSkillContent')));
            } else callback();
          },
          trigger: 'blur'
        }]
      }
    };
  },
  computed: {
    ...mapState({ userInfo: state => state.userInfo }),
    agentOptions() {
      return this.isSuperAdmin ? this.targetAgents : this.agents;
    },
    isSuperAdmin() {
      return Number(this.userInfo && this.userInfo.superAdmin) === 1;
    }
  },
  created() {
    if (this.isSuperAdmin) {
      this.searchUsers('');
      this.loadAdminSkills();
      this.loadRecommendedSkills();
    } else {
      this.loadAgents();
    }
  },
  methods: {
    searchUsers(keyword) {
      if (!this.isSuperAdmin) return;
      this.userSearchLoading = true;
      Api.admin.getUserList({ page: 1, limit: 30, mobile: keyword || '' }, ({ data }) => {
        const response = data;
        const fetchedUsers = response && response.code === 0 && response.data && Array.isArray(response.data.list)
          ? response.data.list
          : [];
        const selectedUser = this.targetUsers.find(user => String(user.userid) === String(this.selectedTargetUserId));
        this.targetUsers = selectedUser && !fetchedUsers.some(user => String(user.userid) === String(selectedUser.userid))
          ? [selectedUser, ...fetchedUsers]
          : fetchedUsers;
        this.userSearchLoading = false;
        if (!response || response.code !== 0) this.$message.error(this.$t('skills.loadUsersFailed'));
        else if (!this.selectedTargetUserId && !String(keyword || '').trim() && this.targetUsers.length) {
          this.selectedTargetUserId = this.targetUsers[0].userid;
          this.handleTargetUserChange(this.selectedTargetUserId);
        }
      });
    },
    handleTargetUserChange(userId) {
      this.selectedAgentId = '';
      this.targetAgents = [];
      this.marketplaceSkills = [];
      if (!userId) return;

      this.targetAgentsLoading = true;
      Api.skills.getAgentsForUser(userId, res => {
        const response = res && res.data;
        this.targetAgents = response && response.code === 0 && Array.isArray(response.data)
          ? response.data
          : [];
        this.targetAgentsLoading = false;
        if (!response || response.code !== 0) {
          this.$message.error((response && response.msg) || this.$t('skills.loadAgentsFailed'));
        } else if (this.targetAgents.length) {
          this.selectedAgentId = this.targetAgents[0].id;
          this.loadMarketplace();
        }
      });
    },
    loadAgents() {
      Api.agent.getAgentList(({ data }) => {
        this.agents = Array.isArray(data && data.data) ? data.data : [];
        if (!this.selectedAgentId && this.agents.length) {
          this.selectedAgentId = this.agents[0].id;
          this.loadMarketplace();
        }
      }, () => this.$message.error(this.$t('skills.loadAgentsFailed')));
    },
    loadMarketplace() {
      if (!this.selectedAgentId) {
        this.marketplaceSkills = [];
        return;
      }
      this.marketplaceLoading = true;
      Api.skills.getMarketplace(this.selectedAgentId, res => {
        const response = res && res.data;
        if (response && response.code === 0) {
          this.marketplaceSkills = Array.isArray(response.data) ? response.data : [];
        } else {
          this.marketplaceSkills = [];
          this.$message.error((response && response.msg) || this.$t('skills.loadFailed'));
        }
        this.marketplaceLoading = false;
      });
    },
    toggleBinding(skill) {
      if (!this.selectedAgentId || this.busySkillId) return;
      if (skill.bound) {
        this.$confirm(this.$t('skills.confirmUnbind', { name: skill.name }), this.$t('common.warning'), {
          confirmButtonText: this.$t('button.ok'),
          cancelButtonText: this.$t('button.cancel'),
          type: 'warning'
        }).then(() => this.updateBinding(skill, false)).catch(() => {});
      } else {
        this.updateBinding(skill, true);
      }
    },
    updateBinding(skill, enabled) {
      this.busySkillId = skill.id;
      const done = res => {
        const response = res && res.data;
        if (response && response.code === 0) {
          this.$message.success(this.$t(enabled ? 'skills.enableSuccess' : 'skills.unbindSuccess'));
          this.loadMarketplace();
        } else {
          this.$message.error((response && response.msg) || this.$t('skills.operationFailed'));
        }
        this.busySkillId = '';
      };
      if (enabled) Api.skills.bindSkill(this.selectedAgentId, skill.id, done);
      else Api.skills.unbindSkill(this.selectedAgentId, skill.id, done);
    },
    loadAdminSkills() {
      this.adminLoading = true;
      Api.skills.getAdminSkills(res => {
        const response = res && res.data;
        this.adminSkills = response && response.code === 0 && Array.isArray(response.data) ? response.data : [];
        if (!response || response.code !== 0) this.$message.error((response && response.msg) || this.$t('skills.loadFailed'));
        this.adminLoading = false;
      });
    },
    loadRecommendedSkills() {
      if (!this.isSuperAdmin) return;
      this.recommendedLoading = true;
      Api.skills.getRecommendedSkillPackages(res => {
        const response = res && res.data;
        this.recommendedSkills = response && response.code === 0 && Array.isArray(response.data)
          ? response.data
          : [];
        if (!response || response.code !== 0) {
          this.$message.error((response && response.msg) || this.$t('skills.loadRecommendedFailed'));
        }
        this.recommendedLoading = false;
      });
    },
    installRecommendedSkill(item) {
      if (this.busyRecommendationKey) return;
      this.$confirm(
        this.$t('skills.confirmInstallRecommended', { name: item.name }),
        this.$t('common.warning'),
        {
          confirmButtonText: this.$t('skills.installRecommended'),
          cancelButtonText: this.$t('button.cancel'),
          type: 'warning'
        }
      ).then(() => {
        this.busyRecommendationKey = item.key;
        Api.skills.installRecommendedSkillPackage(item.key, res => {
          this.busyRecommendationKey = '';
          const response = res && res.data;
          if (response && response.code === 0) {
            this.$message.success(this.$t('skills.recommendedImportedAsDraft'));
            this.loadAdminSkills();
            this.loadRecommendedSkills();
          } else {
            this.$message.error((response && response.msg) || this.$t('skills.installRecommendedFailed'));
          }
        });
      }).catch(() => {});
    },
    openCreateDialog() {
      this.form = emptyForm();
      this.dialogTitle = this.$t('skills.create');
      this.dialogVisible = true;
      this.$nextTick(() => this.$refs.skillForm && this.$refs.skillForm.clearValidate());
    },
    openEditDialog(skill) {
      this.form = { ...emptyForm(), ...skill, status: Number(skill.status) };
      this.dialogTitle = this.$t('skills.edit');
      this.dialogVisible = true;
      this.$nextTick(() => this.$refs.skillForm && this.$refs.skillForm.clearValidate());
    },
    saveSkill() {
      this.$refs.skillForm.validate(valid => {
        if (!valid || this.saving) return;
        this.saving = true;
      const payload = {
          skillCode: this.form.skillCode,
          name: this.form.name,
        description: this.form.description,
        category: this.form.category,
        skillType: this.form.skillType,
        endpointUrl: this.form.endpointUrl,
        skillContent: this.form.skillContent,
        referenceContent: this.form.referenceContent,
        sourceUrl: this.form.sourceUrl,
        license: this.form.license,
        status: Number(this.form.status)
        };
        const finish = res => {
          this.saving = false;
          const response = res && res.data;
          if (response && response.code === 0) {
            this.dialogVisible = false;
            this.$message.success(this.$t('skills.saveSuccess'));
            this.loadAdminSkills();
            this.loadMarketplace();
          } else {
            this.$message.error((response && response.msg) || this.$t('skills.operationFailed'));
          }
        };
        if (this.form.id) Api.skills.updateSkill(this.form.id, payload, finish);
        else Api.skills.createSkill(payload, finish);
      });
    },
    disableSkill(skill) {
      this.$confirm(this.$t('skills.confirmTakeDown', { name: skill.name }), this.$t('common.warning'), {
        confirmButtonText: this.$t('button.ok'),
        cancelButtonText: this.$t('button.cancel'),
        type: 'warning'
      }).then(() => {
        Api.skills.disableSkill(skill.id, res => {
          const response = res && res.data;
          if (response && response.code === 0) {
            this.$message.success(this.$t('skills.takeDownSuccess'));
            this.loadAdminSkills();
            this.loadMarketplace();
          } else {
            this.$message.error((response && response.msg) || this.$t('skills.operationFailed'));
          }
        });
      }).catch(() => {});
    }
  }
};
</script>

<style scoped>
.skills-page-card,
.skills-admin-card {
  margin-bottom: 20px;
}
.skills-header,
.skills-admin-header,
.skill-card-top,
.skill-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.skills-header {
  margin-bottom: 18px;
}
.skills-header h2,
.skills-admin-header h2 {
  margin: 0;
}
.skills-subtitle {
  margin: 8px 0 0;
  color: #7a7f87;
}
.skills-agent-select {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  min-width: 300px;
}
.skills-agent-select .el-select {
  flex: 1;
  min-width: 180px;
}
.full-width {
  width: 100%;
}
.form-tip {
  margin-top: 5px;
  color: #909399;
  font-size: 12px;
  line-height: 1.5;
}
.skills-runtime-notice {
  margin-bottom: 18px;
}
.skills-marketplace {
  min-height: 130px;
}
.skills-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.skill-card h3 {
  margin: 0 0 6px;
  font-size: 16px;
}
.skill-code,
.skill-unbound {
  color: #8a8f98;
  font-size: 12px;
}
.skill-description {
  min-height: 42px;
  margin: 16px 0;
  color: #60656d;
  white-space: pre-wrap;
  word-break: break-word;
}
.skill-provenance {
  display: flex;
  gap: 12px;
  margin: -8px 0 14px;
  color: #8a8f98;
  font-size: 12px;
}
.skills-admin-header {
  margin-bottom: 16px;
}
.recommended-section {
  margin-bottom: 24px;
  padding: 16px;
  border-radius: 8px;
  background: #f7f9fc;
}
.recommended-heading,
.recommended-card-heading,
.recommended-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.recommended-heading {
  margin-bottom: 14px;
}
.recommended-heading h3 {
  margin: 0;
  font-size: 16px;
}
.recommended-heading p,
.recommended-card p {
  margin: 6px 0 0;
  color: #7a7f87;
  font-size: 13px;
  line-height: 1.5;
}
.recommended-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(245px, 1fr));
  gap: 12px;
}
.recommended-card p {
  min-height: 40px;
}
.recommended-card-footer {
  margin-top: 12px;
}
.endpoint-preview {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}
.switch-label {
  margin-left: 10px;
  color: #60656d;
}
@media (max-width: 720px) {
  .skills-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .skills-agent-select {
    width: 100%;
    min-width: 0;
  }
}
</style>

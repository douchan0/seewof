/* 希沃教室控制台 - 前端逻辑 (Vue 3) */
const { createApp, ref, reactive, onMounted, computed } = Vue;

const api = axios.create({ baseURL: '/api/v1' });
api.interceptors.request.use((cfg) => {
  if (localStorage.getItem('token')) {
    cfg.headers.Authorization = 'Bearer ' + localStorage.getItem('token');
  }
  return cfg;
});
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token');
      location.reload();
    }
    return Promise.reject(err);
  }
);

const app = createApp({
  setup() {
    const token = ref(localStorage.getItem('token') || '');
    const me = ref({});
    const loginForm = reactive({ username: '', password: '' });
    const loginLoading = ref(false);
    const activeMenu = ref('classrooms');

    // 数据
    const classrooms = ref([]);
    const usbs = ref([]);
    const logs = ref([]);
    const logTotal = ref(0);
    const logPage = ref(1);
    const logEvents = ref([]);
    const logFilter = reactive({ classroom: '', event: '', q: '' });

    // 排程
    const scheduleClassroom = ref('');
    const scheduleSlots = ref([]);
    const newSlot = reactive({
      weekdays: [0, 1, 2, 3, 4],
      startTime: '08:00',
      endTime: '12:00',
    });

    // 解锁
    const unlockClassroom = ref('');
    const unlockDuration = ref(30);
    const unlockReason = ref('');
    const activeUnlocks = ref([]);

    // ----------------------------------------------------------------- 工具
    const weekdayName = (i) => ['一', '二', '三', '四', '五', '六', '日'][i];
    const formatMin = (m) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;

    // ----------------------------------------------------------------- 登录
    const login = async () => {
      loginLoading.value = true;
      try {
        const r = await api.post('/auth/login',
          new URLSearchParams({
            username: loginForm.username,
            password: loginForm.password,
          }),
          { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }
        );
        token.value = r.data.access_token;
        localStorage.setItem('token', r.data.access_token);
        me.value = r.data.user;
        await loadAll();
      } catch (e) {
        ElementPlus.ElMessage.error('登录失败: ' + (e.response?.data?.detail || e.message));
      } finally {
        loginLoading.value = false;
      }
    };
    const logout = () => { localStorage.removeItem('token'); token.value = ''; };

    // ----------------------------------------------------------------- 教室
    const loadAll = async () => {
      try {
        const [c, u, e] = await Promise.all([
          api.get('/classrooms'),
          api.get('/usb'),
          api.get('/logs/events'),
        ]);
        classrooms.value = c.data;
        usbs.value = u.data;
        logEvents.value = e.data;
      } catch (err) { console.error(err); }
    };

    const openClassroomDialog = (c) => {
      const isEdit = !!c;
      ElementPlus.ElMessageBox.prompt(
        '教室 ID / 名称 (JSON: {id, name, mac, ip, psk})',
        isEdit ? '编辑教室' : '添加教室',
        {
          confirmButtonText: '确定',
          cancelButtonText: '取消',
          inputType: 'textarea',
          inputValue: isEdit
            ? JSON.stringify({ id: c.id, name: c.name, mac: c.mac, ip: c.ip })
            : JSON.stringify({ id: 'ROOM-101', name: '一楼一班', mac: '', ip: '192.168.1.101',
                               psk: 'REPLACE-WITH-48B-RANDOM-BASE64-KEY==', }, null, 2),
        }
      ).then(async ({ value }) => {
        const obj = JSON.parse(value);
        if (isEdit) {
          await api.put('/classrooms/' + c.id, obj);
        } else {
          await api.post('/classrooms', obj);
        }
        await loadAll();
        ElementPlus.ElMessage.success('已保存');
      }).catch(() => {});
    };
    const removeClassroom = async (c) => {
      await ElementPlus.ElMessageBox.confirm(`确认删除 ${c.name}?`, '提示', { type: 'warning' });
      await api.delete('/classrooms/' + c.id);
      await loadAll();
    };

    const openUnlockDialog = (c) => {
      activeMenu.value = 'unlock';
      unlockClassroom.value = c.id;
      loadActiveUnlocks();
    };

    // ----------------------------------------------------------------- 时间表
    const loadSchedules = async () => {
      if (!scheduleClassroom.value) { scheduleSlots.value = []; return; }
      const r = await api.get(`/classrooms/${scheduleClassroom.value}/schedule`);
      if (r.data.length) scheduleSlots.value = r.data[0].slots;
      else scheduleSlots.value = [];
    };
    const addSlot = () => {
      if (!newSlot.weekdays.length) {
        ElementPlus.ElMessage.warning('请选择星期');
        return;
      }
      const [sh, sm] = newSlot.startTime.split(':').map(Number);
      const [eh, em] = newSlot.endTime.split(':').map(Number);
      scheduleSlots.value.push({
        weekdays: [...newSlot.weekdays],
        start_min: sh * 60 + sm,
        end_min: eh * 60 + em,
      });
    };
    const saveSchedule = async () => {
      await api.post(`/classrooms/${scheduleClassroom.value}/schedule`, {
        name: 'default',
        slots: scheduleSlots.value,
      });
      ElementPlus.ElMessage.success('已保存');
      loadSchedules();
    };

    // ----------------------------------------------------------------- 解锁
    const issueUnlock = async () => {
      await api.post(`/classrooms/${unlockClassroom.value}/unlock`, {
        duration_sec: unlockDuration.value * 60,
        reason: unlockReason.value,
      });
      ElementPlus.ElMessage.success('已下发');
      loadActiveUnlocks();
    };
    const loadActiveUnlocks = async () => {
      if (!unlockClassroom.value) { activeUnlocks.value = []; return; }
      const r = await api.get(`/classrooms/${unlockClassroom.value}/unlock/active`);
      activeUnlocks.value = r.data;
    };
    const revokeUnlock = async (c) => {
      await api.delete(`/classrooms/${c.classroom_id}/unlock/${c.command_id}`);
      loadActiveUnlocks();
    };

    // ----------------------------------------------------------------- U 盘
    const openUsbDialog = () => {
      ElementPlus.ElMessageBox.prompt(
        'JSON: {serial, teacher_id, teacher_name, expires_at(可选 ISO)}',
        '添加 U 盘',
        {
          inputType: 'textarea',
          inputValue: JSON.stringify(
            { serial: 'USBSTOR\\DISK&VEN_Kingston&PROD_DataTraveler\\XXXX',
              teacher_id: 'T001', teacher_name: '张老师' },
            null, 2),
        }
      ).then(async ({ value }) => {
        const obj = JSON.parse(value);
        await api.post('/usb', obj);
        await loadAll();
        ElementPlus.ElMessage.success('已添加, 请下载 teacher.key 写入 U 盘根目录');
      }).catch(() => {});
    };
    const downloadKey = async (row) => {
      const r = await api.post(`/usb/${row.id}/sign`, {}, { responseType: 'blob' });
      const url = URL.createObjectURL(new Blob([r.data]));
      const a = document.createElement('a');
      a.href = url; a.download = 'teacher.key'; a.click();
      URL.revokeObjectURL(url);
    };
    const revokeUsb = async (row) => {
      await api.post(`/usb/${row.id}/revoke`);
      await loadAll();
    };
    const removeUsb = async (row) => {
      await ElementPlus.ElMessageBox.confirm('确认删除该 U 盘授权?', '提示', { type: 'warning' });
      await api.delete(`/usb/${row.id}`);
      await loadAll();
    };

    // ----------------------------------------------------------------- 日志
    const loadLogs = async () => {
      const r = await api.get('/logs', {
        params: {
          classroom: logFilter.classroom || undefined,
          event: logFilter.event || undefined,
          q: logFilter.q || undefined,
          limit: 50,
          offset: (logPage.value - 1) * 50,
        },
      });
      logs.value = r.data.items;
      logTotal.value = r.data.total;
    };

    // ----------------------------------------------------------------- 菜单
    const handleMenu = (idx) => {
      activeMenu.value = idx;
      if (idx === 'classrooms') loadAll();
      if (idx === 'logs') loadLogs();
      if (idx === 'schedules') loadSchedules();
      if (idx === 'unlock') loadActiveUnlocks();
    };

    onMounted(async () => {
      if (token.value) {
        try {
          const r = await api.get('/auth/me');
          me.value = r.data;
          await loadAll();
        } catch (e) {
          token.value = '';
          localStorage.removeItem('token');
        }
      }
    });

    return {
      token, me, loginForm, loginLoading, activeMenu, login, logout,
      classrooms, openClassroomDialog, removeClassroom, openUnlockDialog,
      scheduleClassroom, scheduleSlots, newSlot, weekdayName, formatMin,
      addSlot, saveSchedule, loadSchedules,
      unlockClassroom, unlockDuration, unlockReason, issueUnlock,
      activeUnlocks, revokeUnlock, loadActiveUnlocks,
      usbs, openUsbDialog, downloadKey, revokeUsb, removeUsb,
      logs, logTotal, logPage, logEvents, logFilter, loadLogs,
      handleMenu,
    };
  },
});
app.use(ElementPlus);
app.mount('#app');

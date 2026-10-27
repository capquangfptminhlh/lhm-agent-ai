/** LHM Agent AI — self-contained, real-state 3D office.
 * Scene animations visualise only statuses returned by /api/office3d/*.
 * Inspiration: lucwj/ai-agent-3d-prompts (prompt ideas only; no source/assets copied).
 * Runtime Three.js r186 module loaded via HTML import map.
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));
const icons = {
  director:'✦',strategy:'◇',analytics:'▥',research:'⌕',lead:'◎',content:'✎',
  ads:'◖',sales:'♤',crm:'♧',operations:'⚙',procurement:'▣',
  finance:'＄',hr:'♙',support:'♫',quality:'✓',compliance:'⚖'
};
const pretty = {
  idle:'Đang rảnh',running:'Đang thực hiện',queued:'Đang chờ',
  awaiting_approval:'Chờ phê duyệt',completed:'Đã hoàn thành',failed:'Có lỗi',
  cancelled:'Đã hủy',blocked_budget:'Thiếu ngân sách',
  blocked_dependency:'Chờ xử lý phụ thuộc'
};
const statusText = s => pretty[s] || s || 'Chưa có trạng thái';
const statusClass = s => s === 'running' ? 'running'
  : ['queued','awaiting_approval'].includes(s) ? 'awaiting'
  : ['failed','blocked_budget','blocked_dependency','cancelled'].includes(s) ? 'error' : 'idle';
const statusColor = s => s === 'running' ? 0x32d9a4
  : ['queued','awaiting_approval'].includes(s) ? 0xf0b75e
  : ['failed','blocked_budget','blocked_dependency','cancelled'].includes(s) ? 0xf37e91
  : s === 'completed' ? 0x79bbee : 0xbec9d7;

let current = 'demo';
let world = null;
let selected = 'director';
let feed = null;
let pollTimer = null;
let connection = null;
let scene3d = null;
let previouslySynced = false;
let toastTimer = null;

function toast(message, error = false) {
  const el = $('toast');
  el.textContent = message;
  el.className = 'toast show' + (error ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 4200);
}
async function api(path, options = {}) {
  const r = await fetch(path, {cache:'no-store', ...options,
    headers:{'Content-Type':'application/json',...(options.headers||{})}});
  let j;
  try { j = await r.json(); } catch { throw Error('Backend trả dữ liệu không hợp lệ'); }
  if (!r.ok) throw Error(typeof j.detail === 'string' ? j.detail : ('API HTTP '+r.status));
  return j;
}
function compactText(t, max = 130) {
  const str = String(t||'').replace(/\s+/g, ' ').trim();
  return str.length > max ? str.slice(0, max - 1) + '…' : str;
}
function timeText(epoch) {
  return epoch ? new Date(epoch*1000).toLocaleString('vi-VN',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
}
function setConnection(ok, detail = '') {
  const badge = $('liveBadge');
  badge.classList.toggle('off', !ok);
  badge.textContent = ok ? '● LIVE BACKEND' : '● MẤT KẾT NỐI';
  $('railDot').className = 'online-dot ' + (ok ? 'ok' : 'off');
  $('railStatus').textContent = ok ? 'Backend đã kết nối' : (detail || 'Backend ngoại tuyến');
}
function sceneMissing(message) {
  $('sceneLoading').hidden = true;
  $('sceneFallback').hidden = false;
  $('sceneFallback').querySelector('span').textContent = message;
}
const colorHex = n => new THREE.Color(n);
const palette = [
  0x5b89d8,0x34a99f,0xb085db,0xe8a454,
  0x67b1c2,0x8c99d8,0xdf8ea9,0x8db56a
];
const materials = {
  floor: new THREE.MeshStandardMaterial({color:0xe5ebf5,roughness:0.94}),
  trim: new THREE.MeshStandardMaterial({color:0xbecde1,metalness:0.13,roughness:0.75}),
  desk: new THREE.MeshStandardMaterial({color:0xf9fbff,roughness:0.64}),
  dark: new THREE.MeshStandardMaterial({color:0x536c88,metalness:0.25,roughness:0.62}),
  screen: new THREE.MeshStandardMaterial({color:0x193450,metalness:0.05,roughness:0.38}),
  chair: new THREE.MeshStandardMaterial({color:0x526e8f,roughness:0.8}),
  plant: new THREE.MeshStandardMaterial({color:0x559979,roughness:0.92}),
  stem: new THREE.MeshStandardMaterial({color:0x8c745d,roughness:0.93}),
  white: new THREE.MeshStandardMaterial({color:0xffffff,roughness:0.75}),
  glass: new THREE.MeshPhysicalMaterial({color:0xc9e3f8,transparent:true,opacity:.16,
      metalness:.04,roughness:.12,depthWrite:false,side:THREE.DoubleSide}),
};
const GEO = {
  cube: new THREE.BoxGeometry(1,1,1),
  sphere: new THREE.SphereGeometry(1,18,12),
  head: new THREE.SphereGeometry(1,24,16),
  cap: new THREE.SphereGeometry(1,18,10,0,Math.PI*2,0,Math.PI*.57),
  leg: new THREE.CylinderGeometry(.12,.14,1,10),
  arm: new THREE.CylinderGeometry(.10,.12,1,10),
  ring: new THREE.TorusGeometry(.44,.045,8,32),
};
function box(parent, sx,sy,sz,x,y,z,material,cast=false) {
  const mesh=new THREE.Mesh(GEO.cube,material);
  mesh.scale.set(sx,sy,sz); mesh.position.set(x,y,z);
  mesh.receiveShadow=true; mesh.castShadow=cast; parent.add(mesh);return mesh;
}
function sphere(parent, material, x,y,z,sx,sy,sz,geo=GEO.sphere) {
  const mesh = new THREE.Mesh(geo,material);
  mesh.position.set(x,y,z); mesh.scale.set(sx,sy,sz);
  mesh.castShadow=true; parent.add(mesh);return mesh;
}
function textSprite(text, options={}) {
  const canvas=document.createElement('canvas');
  const ctx=canvas.getContext('2d');canvas.width=640;canvas.height=148;
  ctx.fillStyle=options.background||'rgba(255,255,255,.9)';
  ctx.beginPath();ctx.roundRect(6,9,628,130,22);ctx.fill();
  ctx.strokeStyle=options.border||'#d6e6f3';ctx.lineWidth=4;ctx.stroke();
  ctx.textAlign='center';ctx.textBaseline='middle';
  ctx.fillStyle=options.color||'#36526f';
  ctx.font='bold 43px system-ui, sans-serif';
  let value=String(text||'');
  while(ctx.measureText(value).width>570 && value.length>3)value=value.slice(0,-2)+'…';
  ctx.fillText(value,320,78);
  const texture=new THREE.CanvasTexture(canvas);
  texture.colorSpace=THREE.SRGBColorSpace;
  const mat=new THREE.SpriteMaterial({map:texture,depthTest:false,transparent:true});
  const sprite=new THREE.Sprite(mat);sprite.scale.set(options.width||3.5,.80,1);
  sprite.renderOrder=12;return sprite;
}
function pot(parent, x,z,scale=1) {
  const potM = new THREE.MeshStandardMaterial({color:0xe8d9cc,roughness:0.8});
  const leafM = new THREE.MeshStandardMaterial({color:0x579a83,roughness:0.9});
  box(parent,.62*scale,.43*scale,.62*scale,x,.26*scale,z,potM);
  box(parent,.13*scale,.65*scale,.13*scale,x,.72*scale,z,materials.stem);
  for(let k=0;k<5;k++){
    const a=k/5*Math.PI*2;
    sphere(parent,leafM,x+Math.sin(a)*.26*scale,(.92+(k%2)*.18)*scale,z+Math.cos(a)*.26*scale,
           .28*scale,.43*scale,.25*scale);
  }
}
function screenMaterial() {
  return new THREE.MeshStandardMaterial({color:0x244d78,emissive:0x2a628d,emissiveIntensity:.10,
                                        roughness:.36,metalness:.18});
}
function buildDesk(parent,x,z,accent) {
  // One actual workstation for each role, with a desktop screen that glows ONLY while running.
  box(parent,1.75,.12,.87,x,.85,z,materials.desk,true);
  box(parent,.10,.75,.10,x-.70,.43,z-.30,materials.trim);
  box(parent,.10,.75,.10,x+.70,.43,z-.30,materials.trim);
  box(parent,1.45,.09,.16,x,.84,z-.36,materials.trim);
  box(parent,.13,.38,.13,x,1.06,z-.18,materials.dark);
  const screenMat=screenMaterial();
  const monitor=box(parent,.98,.56,.10,x,1.36,z-.27,screenMat,true);
  const led=new THREE.MeshStandardMaterial({color:accent,emissive:accent,emissiveIntensity:.28});
  box(parent,.42,.025,.04,x,1.11,z-.197,led);
  box(parent,.75,.018,.28,x,.925,z+.19,materials.trim);
  // Chair is behind the desk in the accessible 3D aisle.
  const chair=new THREE.Group();chair.position.set(x,0,z+1.08);parent.add(chair);
  box(chair,.66,.17,.63,0,.50,0,materials.chair);
  box(chair,.66,.82,.13,0,.89,-.26,materials.chair);
  box(chair,.11,.48,.11,0,.27,0,materials.dark);
  for(let k=0;k<4;k++){
    const a=Math.PI*.5*k;
    box(chair,.38,.055,.07,Math.cos(a)*.17,.10,Math.sin(a)*.17,materials.dark);
  }
  return {monitor,screenMat,accent};
}
function makeAvatar(role,idx,p) {
  const group=new THREE.Group();group.userData.role=role;
  const skin=[0xf4c7a1,0xe5a982,0xf7c9a7,0xe8b58f,0xb98161][idx%5];
  const hair=[0x322c35,0x574232,0x272e42,0x6c4c39,0x3e3b38][idx%5];
  const shirt=palette[(idx*3)%palette.length];
  const cloth=new THREE.MeshStandardMaterial({color:shirt,roughness:.89});
  const face=new THREE.MeshStandardMaterial({color:skin,roughness:.90});
  const hairstyle=new THREE.MeshStandardMaterial({color:hair,roughness:.92});
  const pants=new THREE.MeshStandardMaterial({color:0x536782,roughness:.95});
  const eyes=new THREE.MeshStandardMaterial({color:0x29374b,roughness:.75});
  sphere(group,cloth,0,.79,0,.39,.47,.34); // chibi torso
  const head=sphere(group,face,0,1.61,.035,.54,.55,.50,GEO.head);
  sphere(group,hairstyle,0,1.77,-.017,.565,.49,.525,GEO.cap);
  const frontHair=sphere(group,hairstyle,0,1.94,.28,.47,.17,.30);
  frontHair.rotation.z=(idx%2?-.16:.17);
  // Face looks toward positive z, toward the camera.
  sphere(group,eyes,-.18,1.62,.49,.053,.069,.034);
  sphere(group,eyes,+.18,1.62,.49,.053,.069,.034);
  const cheek = new THREE.MeshStandardMaterial({color:0xf0a4a9,transparent:true,opacity:.50});
  sphere(group,cheek,-.30,1.49,.43,.095,.042,.02);
  sphere(group,cheek,.30,1.49,.43,.095,.042,.02);
  const smile=new THREE.MeshStandardMaterial({color:0x9f5f5b,roughness:.85});
  sphere(group,smile,0,1.43,.523,.08,.023,.009);
  // Headset badge: technology-worker motif, not a robot.
  const badge=new THREE.MeshStandardMaterial({color:0xeaf2fb,metalness:.12,roughness:.4});
  sphere(group,badge,.20,.95,.302,.086,.078,.035);
  const arms=[];
  [-1,1].forEach(side=>{
    const pivot=new THREE.Group();pivot.position.set(side*.40,1.02,0);group.add(pivot);
    const arm=sphere(pivot,cloth,side*.095,-.20,.025,.16,.31,.17);
    pivot.rotation.z=side*.15;arms.push(pivot);
  });
  [-1,1].forEach(side=>{
    sphere(group,pants,side*.18,.32,0,.17,.31,.19);
    sphere(group,materials.dark,side*.18,.105,.11,.185,.10,.29);
  });
  // Visual ring, color only reflects authoritative state.
  const halo=new THREE.Mesh(GEO.ring,new THREE.MeshBasicMaterial({color:0xbec9d7,transparent:true,opacity:.6}));
  halo.rotation.x=Math.PI/2;halo.position.y=.036;group.add(halo);
  return {group,head,arms,halo,face,cloth,pants};
}
function buildEnvironment(TH,scene) {
  const floor=new THREE.Mesh(new THREE.BoxGeometry(32,.22,21),materials.floor);
  floor.position.y=-.14;floor.receiveShadow=true;scene.add(floor);
  const stripe=new THREE.MeshStandardMaterial({color:0xd2dfed,roughness:.95});
  for(let k=-14;k<15;k+=2.0){
    box(scene,.024,.005,20.9,k,.006,0,stripe);
  }
  for(let k=-10;k<11;k+=2.0)box(scene,31.9,.006,.024,0,.008,k,stripe);
  box(scene,32,.19,.20,0,.04,-10.38,materials.trim);
  box(scene,32,.19,.20,0,.04,10.38,materials.trim);
  box(scene,.22,.19,20.5,-16,.04,0,materials.trim);
  box(scene,.22,.19,20.5,16,.04,0,materials.trim);
  // Mid-aisle for team passage.
  const hall=new THREE.MeshStandardMaterial({color:0xeaf4fb,roughness:.89});
  box(scene,31.7,.018,.86,0,.018,0,hall);
  // Environmental glass and a small skyline, kept behind work areas.
  const wall=new THREE.MeshStandardMaterial({color:0xe7f0fa,roughness:.82});
  box(scene,32,3.1,.17,0,1.55,-10.41,wall);
  const windowMat=new THREE.MeshStandardMaterial({color:0xc8deef,metalness:.04,roughness:.24,
                                                  emissive:0xb5d1e8,emissiveIntensity:.16});
  for(let k=-13.5;k<=13.5;k+=4.5){
    box(scene,3.9,2.0,.035,k,1.8,-10.30,windowMat);
    box(scene,.08,2.05,.06,k-2.0,1.8,-10.24,materials.white);
    box(scene,.08,2.05,.06,k+2.0,1.8,-10.24,materials.white);
  }
  // Branding sign above the back wall.
  const big=textSprite('LHM  ·  AI WORKFORCE',{width:6.1,color:'#2a5d8c',background:'rgba(255,255,255,.91)'});
  big.position.set(0,3.65,-10.1);scene.add(big);
  const rug=new THREE.MeshStandardMaterial({color:0xc8dced,roughness:.9});
  box(scene,31,.027,.21,0,.03,-.05,rug);
  [-15.1,15.1].forEach(x=>{pot(scene,x,-8.9,1.1);pot(scene,x,8.7,1.1)});
}

class LiveOffice3D {
  constructor(host) {
    this.host=host;this.width=0;this.height=0;this.agentObjects=new Map();
    this.roleOrder=[];this.world=null;this.destroyed=false;this.low=false;
    this.last=performance.now();this.lastSelected=null;
    this.clockStart=performance.now()/1000;
    const canvasCheck=document.createElement('canvas');
    if(!canvasCheck.getContext('webgl2') && !canvasCheck.getContext('webgl'))
      throw Error('WebGL không được hỗ trợ trên thiết bị/trình duyệt này');
    this.scene=new THREE.Scene();
    this.scene.background=new THREE.Color(0xdce9f6);
    this.scene.fog=new THREE.FogExp2(0xdce9f6,.014);
    this.camera=new THREE.OrthographicCamera(-18,18,11,-11,.1,200);
    this.camera.position.set(28,30,35);this.camera.lookAt(0,0,0);
    this.camera.zoom=this.host.clientWidth<620?.91:1.20;
    this.camera.updateProjectionMatrix();
    this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'high-performance'});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,1.75));
    this.renderer.outputColorSpace=THREE.SRGBColorSpace;
    this.renderer.toneMapping=THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure=.98;
    this.renderer.shadowMap.enabled=true;
    this.renderer.shadowMap.type=THREE.PCFShadowMap;
    this.renderer.domElement.setAttribute('aria-label','Văn phòng 3D với trạng thái nhân sự từ backend');
    this.host.insertBefore(this.renderer.domElement,this.host.firstChild);
    this.renderer.domElement.addEventListener('webglcontextlost',ev=>{ev.preventDefault();sceneMissing('Ngữ cảnh đồ họa WebGL bị mất. Có thể mở văn phòng 2D để tiếp tục.');});
    this.controls=new OrbitControls(this.camera,this.renderer.domElement);
    this.controls.target.set(0,0.4,0);this.controls.enableDamping=true;
    this.controls.enablePan=false;
    this.controls.enableZoom=true;this.controls.minZoom=.63;this.controls.maxZoom=2.0;
    this.controls.maxPolarAngle=1.45;this.controls.minPolarAngle=.37;
    const ambient=new THREE.HemisphereLight(0xffffff,0xb6cde4,1.45);this.scene.add(ambient);
    const sunlight=new THREE.DirectionalLight(0xffffff,2.1);sunlight.position.set(-12,27,15);
    sunlight.castShadow=true;sunlight.shadow.mapSize.set(2048,2048);
    sunlight.shadow.camera.left=-25;sunlight.shadow.camera.right=25;
    sunlight.shadow.camera.top=24;sunlight.shadow.camera.bottom=-24;
    sunlight.shadow.normalBias=.05;
    this.scene.add(sunlight);
    buildEnvironment(THREE,this.scene);
    this.root=new THREE.Group();this.scene.add(this.root);
    this.raycaster=new THREE.Raycaster();this.pointer=new THREE.Vector2();
    this.down=null;
    this.renderer.domElement.addEventListener('pointerdown',ev=>this.down={x:ev.clientX,y:ev.clientY});
    this.renderer.domElement.addEventListener('pointerup',ev=>{
      if(!this.down)return;
      if(Math.hypot(ev.clientX-this.down.x,ev.clientY-this.down.y)<6)this.pick(ev);
      this.down=null;
    });
    this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(this.host);
    this.resize();this.renderer.setAnimationLoop(t=>this.animate(t));
    this.renderer.domElement.style.touchAction='none';
  }

  createRoom(department,index) {
    const col=index%4,row=Math.floor(index/4);
    const x=-10.6+col*7.07,z=-5.05+row*10.15;
    const group=new THREE.Group();group.position.set(x,0,z);this.root.add(group);
    const tint=new THREE.Color(palette[index%palette.length]);
    const mat=new THREE.MeshStandardMaterial({color:tint.clone().lerp(new THREE.Color(0xf7fbff),.70),roughness:.96});
    const outline=new THREE.MeshStandardMaterial({color:tint,roughness:.67,transparent:true,opacity:.72});
    box(group,6.79,.12,8.42,0,.035,0,mat);
    // Low, transparent glass separators preserve 3D depth.
    box(group,6.75,.55,.045,0,.29,-4.1,materials.glass);
    box(group,.04,.44,7.0,-3.38,.23,0,materials.glass);
    box(group,.04,.44,7.0,3.38,.23,0,materials.glass);
    box(group,6.58,.05,.065,0,.06,-4.1,outline);
    box(group,6.58,.04,.065,0,.06,4.08,outline);
    const title=textSprite(department.name,{width:3.55,
        color:'#2d4d6a',border:'#dae5f1',background:'rgba(255,255,255,.92)'});
    title.position.set(0,2.75,-3.32);group.add(title);
    const p=document.createElement('div');p.className='room-name';p.dataset.room=department.id;
    if(index===0||index===7)pot(group,2.7,-3.2,.9);
    return {group,x,z,index,accent:palette[index%palette.length]};
  }

  build(world) {
    if(this.world && JSON.stringify(this.world.departments) === JSON.stringify(world.departments)){
      this.world=world;return;
    }
    this.root.clear();this.agentObjects.clear();this.world=world;this.roleOrder=[];
    $('actorLabels').replaceChildren();
    world.departments.forEach((department,i)=>{
      const room=this.createRoom(department,i);
      const agents=department.agent_ids.filter(id=>world.agents.some(a=>a.id===id));
      agents.forEach((role,j)=>{
        const n=agents.length;
        const localX=(j-(n-1)/2)*Math.min(2.0,5.2/Math.max(1,n-1));
        const deskZ=-.70;
        const workstation=buildDesk(room.group,localX,deskZ,room.accent);
        const idx=world.agents.findIndex(a=>a.id===role);
        const actor=makeAvatar(role,idx,room.accent);
        room.group.add(actor.group);
        // Resting states stand near the room entrance, not behind an "active" monitor.
        const idle=new THREE.Vector3(localX,0,2.5);
        const work=new THREE.Vector3(localX,0,1.08);
        actor.group.position.copy(idle);
        const label=document.createElement('span');
        label.className='actor-label';label.textContent=world.agents[idx].name;
        $('actorLabels').appendChild(label);
        const item={...actor,id:role,room,idx,station:workstation,idle,work,
                    status:'idle',selected:false,label,transitionAt:0};
        this.agentObjects.set(role,item);
        this.roleOrder.push(role);
      });
    });
  }

  update(world,initial=false) {
    if(!this.world || JSON.stringify(this.world.departments)!==JSON.stringify(world.departments))
      this.build(world);
    this.world=world;
    world.agents.forEach(a=>{
      const item=this.agentObjects.get(a.id);
      if(!item)return;
      const changed=item.status!==a.status;
      if(changed && !initial)item.transitionAt=performance.now();
      item.status=a.status;
      const emissive=a.status==='running' ? .7 : .06;
      item.station.screenMat.emissiveIntensity=emissive;
      const glow=a.status==='running' ? new THREE.Color(0x34dcaf):new THREE.Color(0x29597b);
      item.station.screenMat.emissive.copy(glow);
      item.halo.material.color.setHex(statusColor(a.status));
      item.halo.material.opacity=a.status==='running'? .95:.52;
      item.label.className='actor-label '+statusClass(a.status)+(a.id===selected?' selected':'');
      item.label.textContent=compactText(a.name,20);
    });
  }

  highlight(role) {
    for(const [id, a] of this.agentObjects){
      a.selected=(id===role);
      a.label.classList.toggle('selected',id===role);
      a.halo.scale.setScalar(id===role?1.36:1);
    }
  }

  pick(ev) {
    const rect=this.renderer.domElement.getBoundingClientRect();
    const px=((ev.clientX-rect.left)/rect.width)*2-1,py=-((ev.clientY-rect.top)/rect.height)*2+1;
    this.pointer.set(px,py);
    this.raycaster.setFromCamera(this.pointer,this.camera);
    const hits=this.raycaster.intersectObjects(this.root.children,true);
    for(const hit of hits){
      let p=hit.object;
      while(p && !p.userData?.role)p=p.parent;
      if(p?.userData?.role){selectAgent(p.userData.role);return}
    }
  }

  cameraPreset(type) {
    if(type==='top'){this.camera.position.set(0,49,.01);this.camera.up.set(0,0,-1);this.controls.enableRotate=false;}
    else {this.camera.up.set(0,1,0);this.camera.position.set(27,29,33);this.controls.enableRotate=true;}
    this.controls.target.set(0,.1,0);
    this.controls.update();
    this.resize();
  }

  toggleLow() {
    this.low=!this.low;this.renderer.setPixelRatio(this.low?1:Math.min(window.devicePixelRatio||1,1.75));
    this.renderer.shadowMap.enabled=!this.low;
    this.resize();return this.low;
  }

  resize() {
    const width=Math.max(240,this.host.clientWidth),height=Math.max(250,this.host.clientHeight);
    if(width===this.width&&height===this.height)return;
    this.width=width;this.height=height;
    // Orthographic aspect ratio prevents stretching at narrow mobile widths.
    const base=width<620?19:17.5;
    const aspect=width/height;
    this.camera.left=-base*aspect;
    this.camera.right=base*aspect;
    this.camera.top=base;
    this.camera.bottom=-base;
    // On panoramic desktop viewport, adapt horizontal clipping.
    if(aspect>1.4){this.camera.left=-base*aspect;this.camera.right=base*aspect;}
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width,height,false);
  }

  animate(t) {
    if(this.destroyed)return;
    const seconds=t/1000;
    this.controls.update();
    const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.agentObjects.forEach(a=>{
      const isWorking=a.status==='running';
      const goal=isWorking?a.work:a.idle;
      const speed=reduced?1:.045;
      a.group.position.lerp(goal,speed);
      const moving=a.group.position.distanceTo(goal)>.06;
      if(moving&&!reduced){
        a.group.position.y=Math.abs(Math.sin(seconds*10+a.idx))*.045;
      }else if(isWorking&&!reduced){
        a.group.position.y=Math.sin(seconds*4+a.idx)*.018;
      }else a.group.position.y=0;
      if(isWorking&&!reduced){
        a.arms[0].rotation.x=Math.sin(seconds*8+a.idx)*.22-.7;
        a.arms[1].rotation.x=-Math.sin(seconds*8+a.idx)*.22-.7;
        a.head.rotation.x=.10;
      }else{
        a.arms[0].rotation.x=0;a.arms[1].rotation.x=0;
        a.head.rotation.x=0;
      }
      if(!reduced&&a.status==='completed'&&t-a.transitionAt<1800){
        // Only a real transition into completed triggers the celebratory pose.
        a.arms[0].rotation.z=.72;a.arms[1].rotation.z=-.72;
      }else{a.arms[0].rotation.z=.15;a.arms[1].rotation.z=-.15;}
    });
    // Render billboards in a DOM overlay, so screen readers have the HTML roster.
    const width=this.width,height=this.height;
    this.agentObjects.forEach(a=>{
      const pos=new THREE.Vector3(0,2.56,0);
      a.group.localToWorld(pos);
      pos.project(this.camera);
      const show=pos.z<1&&pos.z>-1&&Math.abs(pos.x)<1.1&&Math.abs(pos.y)<1.1;
      // Avoid overlapping labels: roster already lists every Agent.
      // Highlight selected, running, awaiting approval and blocked states.
      const noteworthy=a.selected || a.status==='running' ||
        ['awaiting_approval','failed','blocked_budget','blocked_dependency'].includes(a.status);
      a.label.style.display=(show&&noteworthy)?'block':'none';
      if(show){
        a.label.style.left=((pos.x+1)*.5*width)+'px';
        a.label.style.top=((1-pos.y)*.5*height)+'px';
      }
    });
    this.renderer.render(this.scene,this.camera);
  }
}

function renderRoster() {
  if(!world)return;
  $('rosterCount').textContent=world.agents.length+' nhân sự';
  $('roster').innerHTML=world.agents.map(a=>`<button type="button" class="roster-btn ${a.id===selected?'active':''}"
     data-role="${esc(a.id)}" role="listitem" aria-label="${esc(a.name)} — ${esc(statusText(a.status))}">
    <span class="mini-avatar">${esc(icons[a.id]||'✦')}</span>
    <span class="info"><strong>${esc(a.name)}</strong><small>${esc(a.department)}</small></span>
    <i class="state-dot ${esc(statusClass(a.status)==='awaiting'?'queued':statusClass(a.status))}"></i>
  </button>`).join('');
  $('roster').querySelectorAll('[data-role]').forEach(b=>{
    b.addEventListener('click',()=>selectAgent(b.dataset.role));
  });
}
function selectAgent(role) {
  if(!world || !world.agents.some(a=>a.id===role))return;
  selected=role;
  renderRoster();renderProfile();
  if(scene3d)scene3d.highlight(selected);
}
function renderProfile() {
  if(!world)return;
  const a=world.agents.find(x=>x.id===selected)||world.agents[0];
  if(!a)return;
  $('selectedDept').textContent=a.department;
  const st=statusClass(a.status);
  $('agentProfile').innerHTML=`<div class="agent-title">
    <span class="portrait">${esc(icons[a.id]||'✦')}</span>
    <div><h3>${esc(a.name)}</h3><p>${esc(a.department)}</p></div>
    </div>
    <div class="agent-status ${esc(st)}">
      <strong>● ${esc(statusText(a.status))}</strong>
      ${a.task_title?esc(compactText(a.task_title,200)):'Chưa có nhiệm vụ đang thực hiện.'}
    </div>
    <p class="muted" style="font-size:11px;line-height:1.6">${esc(a.description)}</p>
    <p style="font-size:10px;color:#8191a3">${a.completed_tasks} việc hoàn thành trên ${a.total_tasks} việc ghi nhận</p>
    <div class="agent-actions"><button type="button" class="primarybtn" id="assignAgent">＋ Giao nhiệm vụ</button>
      <button type="button" class="secondarybtn" id="openComputer">▣ Máy tính Agent</button>
      <button type="button" class="secondarybtn" id="openTeamChat">☏ Giao việc nhóm</button>
      ${a.task_id?'<button type="button" class="secondarybtn" id="showTask">Xem task</button>':''}
    </div>`;
  $('assignAgent').onclick=()=>showAssign(a.id);
  $('openComputer').onclick=()=>window.location.assign('/computer?workspace='+encodeURIComponent(current)+'&role='+encodeURIComponent(a.id));
  $('openTeamChat').onclick=()=>window.location.assign('/team-chat?workspace='+encodeURIComponent(current)+'&starter='+encodeURIComponent(a.id));
  const view=$('showTask');
  if(view)view.onclick=()=>{
    const task=world.tasks.find(x=>x.id===a.task_id);
    toast(task?compactText(task.instruction,190)+' — '+statusText(task.status):'Mở Dashboard để xem nhiệm vụ chi tiết');
  };
}
function renderEvents() {
  if(!world)return;
  const entries=world.events.slice(0,8);
  $('eventList').innerHTML=entries.map(e=>`<div class="event">
      <span class="mark">◷</span>
      <div><strong>${esc(e.event_type)}</strong><p>${esc(compactText(e.summary,250))}</p></div>
      <time>${esc(timeText(e.timestamp))}</time>
    </div>`).join('')||'<div class="empty-card">Chưa có sự kiện ghi nhận trong backend.</div>';
}
function renderStats() {
  if(!world)return;
  const st=world.statuses||{},money=world.financials||{};
  $('agentCount').textContent=String(world.agents.length);
  $('runningCount').textContent=String(st.running||0);
  $('queueCount').textContent=String((st.queued||0)+(st.awaiting_approval||0))+' đang chờ';
  $('doneCount').textContent=String(st.completed||0);
  $('costCount').textContent='$'+Number(money.recorded_upper_cost_usd||0).toFixed(4);
  $('workerPill').textContent=world.worker?.online?'WORKER LIVE':'WORKER OFFLINE';
  $('workerLabel').textContent=world.worker?.online?'Đang hoạt động':'Chưa phát heartbeat';
  $('pendingTools').textContent=String(world.tool_summary?.counts?.awaiting_approval||0);
  $('lastSync').textContent='Đồng bộ lúc '+new Date().toLocaleTimeString('vi-VN');
}
function applySnapshot(data) {
  if(!data || data.schema_version!==1 || data.mode!=='live_backend' || !Array.isArray(data.agents))
    throw Error('Snapshot không đúng giao thức Live LHM');
  if(data.workspace?.id!==current)return;
  const first=!previouslySynced;
  world=data;previouslySynced=true;
  if(scene3d){
    scene3d.update(data,first);
    scene3d.highlight(selected);
  }
  renderStats();renderRoster();renderProfile();renderEvents();setConnection(true);
}
async function sync() {
  try{
    const result=await api('/api/office3d/snapshot?workspace_id='+encodeURIComponent(current));
    applySnapshot(result);
  }catch(err){
    setConnection(false,err.message);
    if(!world)toast('Không lấy được trạng thái backend: '+err.message,true);
  }
}
function connectEvents() {
  if(feed){feed.close();feed=null;}
  if(typeof EventSource==='undefined')return;
  const source=new EventSource('/api/office3d/events?workspace_id='+encodeURIComponent(current));
  feed=source;
  source.addEventListener('snapshot',ev=>{
    if(feed!==source)return;
    try{applySnapshot(JSON.parse(ev.data));}catch(err){toast(err.message,true);}
  });
  source.addEventListener('offline',()=>{
    if(feed!==source)return;
    setConnection(false,'Stream offline');
  });
  source.onerror=()=>{
    if(feed!==source)return;
    // EventSource reconnects automatically. The periodic snapshot poll is
    // still active; never create fake idle tasks during reconnection.
    $('lastSync').textContent='SSE gián đoạn · dự phòng bằng polling';
  };
}
function showAssign(role) {
  if(!world)return;
  $('taskRole').innerHTML=world.agents.map(a=>`<option value="${esc(a.id)}"
    ${a.id===role?'selected':''}>${esc(a.name)} — ${esc(a.department)}</option>`).join('');
  $('taskInstruction').value='';$('taskContext').value='';
  $('taskApproval').checked=false;
  $('taskDialog').showModal();
  $('taskInstruction').focus();
}
$('taskForm').addEventListener('submit',async ev=>{
  ev.preventDefault();
  const btn=$('submitTask');
  btn.disabled=true;
  const role=$('taskRole').value, instruction=$('taskInstruction').value.trim();
  try{
    const body={role,instruction,context:$('taskContext').value.trim(),
                requires_approval:$('taskApproval').checked,
                idempotency_key:'office3d-'+(crypto.randomUUID?crypto.randomUUID():String(Date.now()))};
    const result=await api('/workspaces/'+encodeURIComponent(current)+'/tasks',{
      method:'POST',body:JSON.stringify(body)});
    $('taskDialog').close();await sync();
    toast('Đã tạo nhiệm vụ '+result.id.slice(0,8)+' · '+statusText(result.status));
  }catch(err){toast('Không tạo được nhiệm vụ: '+err.message,true);}
  finally{btn.disabled=false;}
});
$('closeDialog').onclick=()=>$('taskDialog').close();
$('cancelDialog').onclick=()=>$('taskDialog').close();
$('syncBtn').onclick=()=>sync();
$('camIso').onclick=()=>{
  scene3d?.cameraPreset('iso');
  $('camIso').classList.add('selected');$('camTop').classList.remove('selected');
};
$('camTop').onclick=()=>{
  scene3d?.cameraPreset('top');
  $('camTop').classList.add('selected');$('camIso').classList.remove('selected');
};
$('qualityToggle').onclick=()=>{
  if(!scene3d)return toast('3D chưa tải xong',true);
  const low=scene3d.toggleLow();
  $('qualityToggle').classList.toggle('selected',low);
  toast(low?'Đã bật chế độ đồ họa nhẹ':'Đã bật chất lượng 3D cao hơn');
};
$('workspace').onchange=async ev=>{
  current=ev.target.value;
  previouslySynced=false;world=null;
  connectEvents();await sync();
};
async function init(){
  try {
    const [companies,health] = await Promise.all([api('/workspaces'),api('/health')]);
    const list=companies.workspaces||[];
    const params=new URLSearchParams(location.search);
    const wanted=params.get('workspace');
    current=list.some(w=>w.id===wanted)?wanted:list.some(w=>w.id==='demo')?'demo':list[0]?.id;
    if(!current)throw Error('Chưa có workspace');
    $('workspace').innerHTML=list.map(w=>`<option value="${esc(w.id)}">${esc(w.name)}</option>`).join('');
    $('workspace').value=current;
    $('providerLabel').textContent=String(health.active_model||health.llm_provider||'Chưa cấu hình');
    await sync();
    connectEvents();
    pollTimer=setInterval(sync,12000);
    try {
      scene3d=new LiveOffice3D($('canvasHost'));
      if(world){scene3d.update(world,true);scene3d.highlight(selected);}
      $('sceneLoading').hidden=true;
    }catch(error){
      sceneMissing(error.message+' · Three.js cần WebGL và CDN hoạt động.');
      toast('Không khởi động được WebGL; văn phòng 2D vẫn dùng được.',true);
    }
  }catch(err){
    setConnection(false,err.message);
    sceneMissing('Không kết nối được backend: '+err.message);
  }
}
init();

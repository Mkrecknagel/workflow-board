// Browser-test helper: real instance boards live in their projects (registered in project.json), not in boards/.
const cp = require('child_process'), path = require('path');
const PY = path.join(__dirname, '..', '_real.py');
const py = args => cp.execFileSync(process.env.PYTHON || 'python', [PY, ...args], { encoding: 'utf8' });
exports.addInstances = wbBoards => { py(['add', wbBoards]); };
exports.realPaths = () => Object.values(JSON.parse(py(['paths'])));

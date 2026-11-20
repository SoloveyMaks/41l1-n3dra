import asyncio
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from backend.app.main import app


async def test_full_flow():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        # 1. Load sample artifact
        art_res = await client.post('/api/artifacts/sample')
        assert art_res.status_code == 200, f'Art upload failed: {art_res.text}'
        art = art_res.json()
        print('1. Sample artifact loaded:', art['id'], 'Files:', art['file_count'])

        # 2. Get default workflow
        wf_res = await client.get('/api/workflows')
        assert wf_res.status_code == 200
        wfs = wf_res.json()
        wf_id = wfs[0]['id']
        print('2. Default workflow loaded:', wf_id, 'Nodes:', len(wfs[0]['draft']['nodes']))

        # 3. Trigger run
        run_res = await client.post(f'/api/workflows/{wf_id}/runs', json={'artifactId': art['id']})
        assert run_res.status_code == 202
        run_data = run_res.json()
        run_id = run_data['runId']
        print('3. Run triggered:', run_id)

        # 4. Wait for run completion
        st = {}
        for _ in range(40):
            await asyncio.sleep(0.5)
            check_res = await client.get(f'/api/runs/{run_id}')
            st = check_res.json()
            if st['execution_status'] in ('succeeded', 'failed'):
                break

        print('4. Run finished with status:', st.get('execution_status'), 'Verdict:', st.get('security_verdict'))
        assert st.get('execution_status') == 'succeeded', f'Run did not succeed: {st}'
        assert st.get('security_verdict') == 'fail', f"Expected fail verdict, got {st.get('security_verdict')}"

        # 5. Check findings
        find_res = await client.get(f'/api/runs/{run_id}/findings')
        findings = find_res.json()
        print('5. Total findings produced:', len(findings))
        analyzers_found = set(f['analyzer'] for f in findings)
        print('   Analyzers represented in findings:', analyzers_found)
        assert len(analyzers_found) == 3, f'Expected all 3 analyzers, got: {analyzers_found}'

        # 6. Check report & Security Passport
        rep_res = await client.get(f'/api/runs/{run_id}/report')
        assert rep_res.status_code == 200
        report = rep_res.json()
        passport = report['passport']
        print('6. Security Passport verified:')
        print('   - Files scanned:', passport['files_scanned'])
        print('   - Rules executed:', len(passport['rules_executed']))
        print('   - Completeness score:', passport['completeness_score'])
        print('   - Confirmed findings:', passport['confirmed_findings'])
        print('   - AI Status:', report['ai_status'])

        # 7. Check node runs
        nodes_res = await client.get(f'/api/runs/{run_id}/nodes')
        node_runs = nodes_res.json()
        print('7. Executed nodes count:', len(node_runs))
        for nr in node_runs:
            print(f"   Node {nr['node_id']} ({nr['kind']}): {nr['status']} ({nr['duration_ms']}ms)")

        # 8. Check events
        events_res = await client.get(f'/api/runs/{run_id}/report/official.json')
        assert events_res.status_code == 200
        print('8. Official JSON downloaded successfully, length:', len(events_res.text))

        print('\n>>> ALL BACKEND PIPELINE CHECKS PASSED SUCCESSFULLY! <<<')


if __name__ == '__main__':
    asyncio.run(test_full_flow())
